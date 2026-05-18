"""Pydantic-based file readers: BaseReader, CSVReader, ExcelReader, MATReader.

Each reader subclass owns the parse → resolve → normalize pipeline for one file
extension family. Readers expose pydantic fields for I/O configuration and use
the same :func:`bdf2.normalize` step internally to produce BDF-canonical output.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self

import polars as pl
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    model_validator,
)

from ._normalize import normalize
from .schema import MetadataParser, Normalizer, ResolvedColumn, Source, _SPEC_COLUMNS
from .sources import REGISTRY, get_normalizer


def _sniff_decimal(df: pl.DataFrame | pl.LazyFrame) -> str:
    """Return ',' if comma-decimal strings dominate string columns, else '.'."""
    sample = df.head(1000).collect() if isinstance(df, pl.LazyFrame) else df.head(1000)
    comma = dot = 0
    for col in sample.columns:
        if sample[col].dtype in (pl.String, pl.Utf8):
            comma += int(sample[col].str.count_matches(r"\d+,\d+").sum())
            dot += int(sample[col].str.count_matches(r"\d+\.\d+").sum())
    return "," if comma > dot else "."


def _coerce_decimal(lf: pl.LazyFrame, decimal: str) -> pl.LazyFrame:
    """Replace non-standard decimal separator in string columns."""
    if decimal == ".":
        return lf
    schema = lf.collect_schema()
    exprs = [
        pl.col(c).str.replace_all(decimal, ".", literal=True).alias(c)
        if dtype in (pl.String, pl.Utf8) else pl.col(c)
        for c, dtype in schema.items()
    ]
    return lf.select(exprs)


def _coerce_source(v: Any) -> Any:
    if v is None or isinstance(v, Source):
        return v
    if isinstance(v, str):
        try:
            return get_normalizer(v)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
    raise ValueError(f"source must be Source | str | None, got {type(v).__name__}")


SourceField = Annotated[Source | None, BeforeValidator(_coerce_source)]


def _read_sample_bytes(path: Path, n_bytes: int = 65536) -> str:
    with open(path, "rb") as fh:
        raw = fh.read(n_bytes)
    text = raw.decode("utf-8", errors="replace")
    last_nl = text.rfind("\n")
    if last_nl >= 0:
        text = text[:last_nl]
    return text


def _numeric_ratio(line: str, sep: str) -> float:
    fields = line.split(sep)
    if not fields:
        return 0.0
    hits = 0
    for f in fields:
        try:
            float(f.strip())
            hits += 1
        except ValueError:
            pass
    return hits / len(fields)


def _detect_layout(
    sample: str,
    candidates: tuple[str, ...] = (",", "\t", ";", "|"),
    min_run: int = 5,
) -> tuple[str, int, int, bool]:
    """Return (separator, header_idx, data_start_idx, has_header) from a sample string."""
    lines = sample.splitlines()
    best_sep = ","
    best_run_len = 0
    best_field_count = 0
    best_run_start = 0

    for sep in candidates:
        field_counts = [
            n if (n := len(line.rstrip(sep).split(sep))) >= 2 else 0
            for line in lines
        ]
        i = 0
        while i < len(field_counts):
            if field_counts[i] == 0:
                i += 1
                continue
            fc = field_counts[i]
            j = i
            while j < len(field_counts) and field_counts[j] == fc:
                j += 1
            run_len = j - i
            score = run_len * fc
            best_score = best_run_len * best_field_count
            if score > best_score or (score == best_score and run_len > best_run_len):
                best_sep = sep
                best_run_len = run_len
                best_field_count = fc
                best_run_start = i
            i = j

    if best_run_len < min_run:
        return (",", 0, 0, True)

    header_idx = best_run_start
    data_start_idx = best_run_start + 1
    header_line = lines[header_idx] if header_idx < len(lines) else ""
    data_line = lines[data_start_idx] if data_start_idx < len(lines) else ""
    h = _numeric_ratio(header_line, best_sep)
    d = _numeric_ratio(data_line, best_sep)
    has_header = (h < 0.3) and (d > 0.6)
    return (best_sep, header_idx, data_start_idx, has_header)


def _resolve_source_for(
    explicit: Source | None,
    head_bytes: bytes,
    headers: list[str],
) -> Source | None:
    if explicit is not None:
        return explicit
    magic_matches = [n for n in REGISTRY if n.match_magic(head_bytes)]
    if len(magic_matches) == 1:
        return magic_matches[0]
    pool = magic_matches if magic_matches else REGISTRY
    best: Source | None = None
    best_score = 0
    for n in pool:
        sc = n.score(headers)
        if sc > best_score:
            best = n
            best_score = sc
    return best


def _strip_all_suffixes(path: Path) -> str:
    name = path.name
    while True:
        suffix = Path(name).suffix
        if not suffix:
            break
        name = Path(name).stem
    return name


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"failed to parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"reader config must be a JSON object: {path}")
    return data


def _find_contribution_config(start: Path, kind: str) -> dict[str, Any] | None:
    current = start.resolve()
    while True:
        for name in ("contribution.json", "collection.json"):
            candidate = current / name
            if candidate.exists():
                raw = _load_json_file(candidate)
                cfg = raw.get(kind)
                if isinstance(cfg, dict):
                    return cfg
        if current.parent == current:
            return None
        current = current.parent


def _discover_config(path: Path, kind: str) -> dict[str, Any] | None:
    env_var = f"BDF_{kind.upper()}_CONFIG"
    env_path = os.environ.get(env_var)
    if env_path:
        return _load_json_file(Path(env_path))
    base = _strip_all_suffixes(path)
    candidates = [
        path.with_name(f"{base}.{kind}.json"),
        path.with_name(f"bdf.{kind}.json"),
        path.with_name(f"{kind}.json"),
    ]
    for cand in candidates:
        if cand.exists():
            return _load_json_file(cand)
    return _find_contribution_config(path.parent, kind)


class BaseReader(BaseModel):
    """Pydantic base for file readers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: ClassVar[str] = ""

    source: SourceField = None
    include_optional: bool = True
    extra_columns: dict[str, str] | None = None

    _config_loaded: bool = PrivateAttr(default=False)

    def _parse(self, path: Path) -> tuple[pl.LazyFrame, list[str]]:
        """Subclass hook: return (lazy_frame, preamble_lines)."""
        raise NotImplementedError

    def _fill_from_config_file(self, path: Path) -> None:
        """Merge config-file values into fields not explicitly set on this instance."""
        if self._config_loaded:
            return
        cfg = _discover_config(path, self.kind)
        self._config_loaded = True
        if not cfg:
            return
        set_fields = self.model_fields_set
        for k, v in cfg.items():
            if k in set_fields or k not in type(self).model_fields:
                continue
            with contextlib.suppress(Exception):
                setattr(self, k, v)

    @classmethod
    def from_config_file(cls, path: str | Path) -> Self:
        data = _load_json_file(Path(path))
        return cls.model_validate(data)

    def read(
        self,
        path: str | Path,
        *,
        lazy: bool = False,
        column_map: dict[str, str] | None = None,
    ) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
        """Read ``path`` and return ``(df, metadata)`` in BDF canonical form."""
        path = Path(path)
        if not self.model_fields_set or self.model_fields_set <= {"source"}:
            self._fill_from_config_file(path)
        lf, preamble = self._parse(path)
        head_bytes = ("\n".join(preamble)).encode("utf-8", errors="replace")
        if not head_bytes:
            try:
                head_bytes = _read_sample_bytes(path, 8192).encode("utf-8", errors="replace")
            except Exception:
                head_bytes = b""
        schema = lf.collect_schema()
        headers = list(schema.names())
        resolved_source = _resolve_source_for(self.source, head_bytes, headers)
        if "decimal" in type(self).model_fields:
            decimal: str = self.decimal or _sniff_decimal(lf)  # type: ignore[attr-defined]
            lf = _coerce_decimal(lf, decimal)
        bdf_lf = normalize(
            lf,
            source=resolved_source,
            include_optional=self.include_optional,
            column_map=column_map,
            extra_columns=self.extra_columns,
        )
        metadata: dict = {}
        if resolved_source is not None and preamble:
            for key, val in resolved_source.metadata.parse(preamble).items():
                metadata[key] = val
        if lazy:
            return bdf_lf, metadata
        return bdf_lf.collect() if isinstance(bdf_lf, pl.LazyFrame) else bdf_lf, metadata


class CSVReader(BaseReader):
    """Reader for delimited text files (.csv/.tsv/.txt/.dat)."""

    kind: ClassVar[str] = "csv"

    separator: str | None = None
    skip_rows: int | None = None
    has_header: bool | None = None
    decimal: str | None = None
    encoding: str = "utf-8"

    def _parse(self, path: Path) -> tuple[pl.LazyFrame, list[str]]:
        sample = _read_sample_bytes(path)
        sep_sn, skip_sn, _, has_sn = _detect_layout(sample)
        sep = self.separator if self.separator is not None else sep_sn
        skip = self.skip_rows if self.skip_rows is not None else skip_sn
        has = self.has_header if self.has_header is not None else has_sn
        preamble = sample.splitlines()[:skip]
        encoding_arg = "utf8" if self.encoding.lower() in ("utf-8", "utf8") else "utf8-lossy"
        lf = pl.scan_csv(
            path,
            skip_rows=skip,
            separator=sep,
            has_header=has,
            infer_schema=False,
            encoding=encoding_arg,
        )
        return lf, preamble


class ExcelReader(BaseReader):
    """Reader for .xlsx/.xlsm/.xls via polars.read_excel (calamine engine)."""

    kind: ClassVar[str] = "excel"

    sheet: str | int = 1
    header_row: int = 0
    skiprows: int = 0
    nrows: int | None = None
    usecols: list[int] | list[str] | str | None = None
    has_header: bool = True
    engine: Literal["calamine", "openpyxl", "xlsx2csv"] = "calamine"
    rename: dict[str, str] | None = None
    drop_empty_rows: bool = True
    strip_headers: bool = True

    def _parse(self, path: Path) -> tuple[pl.LazyFrame, list[str]]:
        read_options: dict[str, Any] = {}
        if self.header_row:
            read_options["header_row"] = self.header_row
        if self.skiprows:
            read_options["skip_rows"] = self.skiprows
        if self.nrows is not None:
            read_options["n_rows"] = self.nrows

        kwargs: dict[str, Any] = {
            "engine": self.engine,
            "has_header": self.has_header,
            "drop_empty_rows": self.drop_empty_rows,
        }
        if isinstance(self.sheet, int):
            kwargs["sheet_id"] = self.sheet
        else:
            kwargs["sheet_name"] = self.sheet
        if self.usecols is not None:
            kwargs["columns"] = self.usecols
        if read_options:
            kwargs["read_options"] = read_options

        try:
            df = pl.read_excel(path, **kwargs)
        except ImportError as exc:
            raise RuntimeError(
                "ExcelReader requires fastexcel for the calamine engine. "
                "Install with `pip install fastexcel`."
            ) from exc

        if isinstance(df, dict):
            raise ValueError(
                "ExcelReader expects a single sheet; specify `sheet` to disambiguate."
            )

        if self.rename:
            df = df.rename(self.rename)
        if self.strip_headers:
            df = df.rename({c: str(c).strip().lstrip("﻿") for c in df.columns})
        df = df.with_columns(pl.all().cast(pl.Utf8, strict=False))
        return df.lazy(), []


def _coerce_resolved_value(val: Any, unit: str) -> ResolvedColumn:
    if isinstance(val, ResolvedColumn):
        return val
    if isinstance(val, str):
        return ResolvedColumn(source_header=val, scale=1.0, offset=0.0)
    if isinstance(val, (tuple, list)):
        if len(val) == 2:
            return ResolvedColumn(source_header=val[0], scale=float(val[1]), offset=0.0)
        if len(val) == 3:
            return ResolvedColumn(source_header=val[0], scale=float(val[1]), offset=float(val[2]))
        raise ValueError(
            f"MATReader.column_map value must be str | tuple-2 | tuple-3 | ResolvedColumn; got {val!r}"
        )
    if isinstance(val, dict):
        return ResolvedColumn.model_validate({k: v for k, v in val.items() if k != "bdf_unit"})
    raise TypeError(f"unsupported MATReader.column_map value type: {type(val).__name__}")


def _coerce_bdfcolumn_key(k: Any) -> str:
    """Return the BDF mr_name for k, accepting mr_name or SCREAMING_SNAKE_CASE."""
    if isinstance(k, str):
        if k in _SPEC_COLUMNS:
            return k
        lowered = k.lower()
        if lowered in _SPEC_COLUMNS:
            return lowered
    raise ValueError(f"MATReader.column_map key {k!r} is not a valid BDF mr_name")


def _build_mat_normalizer(raw: dict[Any, Any]) -> Normalizer:
    """Convert a column_map dict into a Normalizer with ResolvedColumn fields."""
    kwargs: dict[str, ResolvedColumn] = {}
    seen_headers: set[str] = set()
    for k, val in raw.items():
        mr_name = _coerce_bdfcolumn_key(k)
        unit = str(_SPEC_COLUMNS[mr_name]["unit"])
        rc = _coerce_resolved_value(val, unit)
        if rc.source_header in seen_headers:
            raise ValueError(
                f"MATReader.column_map: duplicate source_header {rc.source_header!r}"
            )
        seen_headers.add(rc.source_header)
        kwargs[mr_name] = rc
    if not kwargs:
        raise ValueError("MATReader requires a non-empty column_map")
    return Normalizer(**kwargs)


class MATReader(BaseReader):
    """Reader for .mat (MATLAB) files via scipy.io.loadmat."""

    kind: ClassVar[str] = "mat"

    normalizer: Normalizer = Field(default_factory=Normalizer)
    variable_names: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_column_map(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        column_map = data.pop("column_map", None)
        if column_map is not None:
            normalizer = _build_mat_normalizer(column_map)
            data["normalizer"] = normalizer
            data.setdefault("source", Source(id="mat", normalizer=normalizer, metadata=MetadataParser()))
        return data

    @model_validator(mode="after")
    def _validate_normalizer(self) -> "MATReader":
        if not any(True for _ in self.normalizer):
            raise ValueError("MATReader requires a non-empty column_map")
        if self.source is None:
            object.__setattr__(self, "source", Source(id="mat", normalizer=self.normalizer, metadata=MetadataParser()))
        return self

    def _parse(self, path: Path) -> tuple[pl.LazyFrame, list[str]]:
        try:
            import numpy as np
            from scipy.io import loadmat
        except ImportError as exc:
            raise RuntimeError(
                "MATReader requires scipy. Install with `pip install scipy`."
            ) from exc
        rc_entries = [rc for _, rc in self.normalizer if isinstance(rc, ResolvedColumn)]
        var_names = (
            self.variable_names
            if self.variable_names is not None
            else [rc.source_header for rc in rc_entries]
        )
        mat = loadmat(str(path), variable_names=var_names, squeeze_me=True)
        data: dict[str, Any] = {}
        for rc in rc_entries:
            if rc.source_header not in mat:
                raise ValueError(
                    f"MATReader: variable {rc.source_header!r} not found in {path}"
                )
            arr = np.atleast_1d(np.asarray(mat[rc.source_header]).squeeze())
            if arr.ndim != 1:
                raise ValueError(
                    f"MATReader: variable {rc.source_header!r} has shape "
                    f"{arr.shape} after squeeze; must be 1-D"
                )
            data[rc.source_header] = arr.astype(np.float64)
        df = pl.DataFrame(data)
        return df.lazy(), []


__all__ = ["BaseReader", "CSVReader", "ExcelReader", "MATReader"]
