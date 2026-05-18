"""Pydantic-based file readers: BaseReader, CSVReader, ExcelReader, MATReader.

Each reader subclass owns the parse → resolve → normalize pipeline for one file
extension family. Readers expose pydantic fields for I/O configuration and use
the same :func:`bdf2.normalize` step internally to produce BDF-canonical output.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self

import polars as pl
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from ._normalize import normalize
from .schema import BDFColumn, Normalizer, ResolvedColumn
from .sources import REGISTRY, get_normalizer


def _coerce_source(v: Any) -> Any:
    if v is None or isinstance(v, Normalizer):
        return v
    if isinstance(v, str):
        try:
            return get_normalizer(v)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
    raise ValueError(f"source must be Normalizer | str | None, got {type(v).__name__}")


SourceField = Annotated[Normalizer | None, BeforeValidator(_coerce_source)]


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
    explicit: Normalizer | None,
    head_bytes: bytes,
    headers: list[str],
) -> Normalizer | None:
    if explicit is not None:
        return explicit
    magic_matches = [n for n in REGISTRY if n.match_magic(head_bytes)]
    if len(magic_matches) == 1:
        return magic_matches[0]
    pool = magic_matches if magic_matches else REGISTRY
    best: Normalizer | None = None
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
        """Read ``path`` and return ``(df, metadata)`` in BDF canonical form.

        Pipeline:

        1. If no reader fields are explicitly set (except ``source``), search for
           a sibling JSON config and merge it into unset fields.

           Search order (per reader ``kind``):

           - ``$BDF_<KIND>_CONFIG`` environment variable
           - ``<stem-stripped-of-all-suffixes>.<kind>.json`` next to the data file
           - ``bdf.<kind>.json`` next to the data file
           - ``<kind>.json`` next to the data file
           - ``contribution.json`` / ``collection.json`` walked from the data
             directory up to filesystem root, picking the ``<kind>`` sub-object

        2. Subclass ``_parse(path)`` returns a ``pl.LazyFrame`` of ``Utf8``
           columns plus any preamble lines.
        3. Source resolution: an explicit ``self.source`` wins; otherwise the
           file head bytes (preamble or first 8 KB) are tested against every
           ``Normalizer.match_magic`` and the highest-scoring ``Normalizer`` is
           selected (with magic matches preferred when at least one matches).
        4. :func:`bdf2.normalize` rewrites the columns to BDF ``mr_name``
           identifiers, applying pint scale/offset, decimal-separator sniffing,
           and dtype coercion.
        5. ``metadata_patterns`` from the resolved source are matched against
           the preamble; matches land in the returned metadata dict.
        """
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
        decimal = getattr(self, "decimal", None)
        bdf_lf, metadata = normalize(
            lf,
            source=resolved_source,
            include_optional=self.include_optional,
            column_map=column_map,
            extra_columns=self.extra_columns,
            decimal=decimal,
        )
        if resolved_source is not None and preamble:
            for key, pattern in resolved_source.metadata_patterns.items():
                rx = re.compile(pattern, re.IGNORECASE)
                for line in preamble:
                    m = rx.search(line)
                    if m:
                        metadata[key] = m.group(1).strip()
                        break
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


def _coerce_resolved_value(val: Any, col: BDFColumn) -> ResolvedColumn:
    if isinstance(val, ResolvedColumn):
        return val
    if isinstance(val, str):
        return ResolvedColumn(
            source_header=val, bdf_unit=col.unit, scale=1.0, offset=0.0,
        )
    if isinstance(val, (tuple, list)):
        if len(val) == 2:
            return ResolvedColumn(
                source_header=val[0], bdf_unit=col.unit, scale=float(val[1]), offset=0.0,
            )
        if len(val) == 3:
            return ResolvedColumn(
                source_header=val[0], bdf_unit=col.unit,
                scale=float(val[1]), offset=float(val[2]),
            )
        raise ValueError(
            f"MATReader.column_map value must be str | tuple-2 | tuple-3 | ResolvedColumn"
            f"; got {val!r}"
        )
    if isinstance(val, dict):
        return ResolvedColumn.model_validate({"bdf_unit": col.unit, **val})
    raise TypeError(f"unsupported MATReader.column_map value type: {type(val).__name__}")


def _coerce_bdfcolumn_key(k: Any) -> BDFColumn:
    if isinstance(k, BDFColumn):
        return k
    if isinstance(k, str):
        if k in BDFColumn.__members__:
            return BDFColumn[k]
        for c in BDFColumn:
            if c.mr_name == k:
                return c
    raise ValueError(f"MATReader.column_map key {k!r} is not a BDFColumn / mr_name / enum name")


class MATReader(BaseReader):
    """Reader for .mat (MATLAB) files via scipy.io.loadmat."""

    kind: ClassVar[str] = "mat"

    column_map: dict[BDFColumn, ResolvedColumn] = Field(default_factory=dict)
    variable_names: list[str] | None = None

    @field_validator("column_map", mode="before")
    @classmethod
    def _coerce_column_map(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        out: dict[BDFColumn, ResolvedColumn] = {}
        for k, val in v.items():
            col = _coerce_bdfcolumn_key(k)
            out[col] = _coerce_resolved_value(val, col)
        return out

    @model_validator(mode="after")
    def _check_column_map(self) -> "MATReader":
        if not self.column_map:
            raise ValueError("MATReader requires a non-empty column_map")
        seen: dict[str, BDFColumn] = {}
        for col, rc in self.column_map.items():
            if rc.bdf_unit and rc.bdf_unit != col.unit:
                raise ValueError(
                    f"{col.name}: column_map bdf_unit {rc.bdf_unit!r} does not match "
                    f"BDFColumn unit {col.unit!r}"
                )
            if rc.source_header in seen:
                raise ValueError(
                    f"MATReader.column_map: duplicate source_header {rc.source_header!r} "
                    f"used by {seen[rc.source_header].name} and {col.name}"
                )
            seen[rc.source_header] = col
        return self

    def _parse(self, path: Path) -> tuple[pl.LazyFrame, list[str]]:
        try:
            import numpy as np
            from scipy.io import loadmat
        except ImportError as exc:
            raise RuntimeError(
                "MATReader requires scipy. Install with `pip install scipy`."
            ) from exc
        var_names = (
            self.variable_names
            if self.variable_names is not None
            else [rc.source_header for rc in self.column_map.values()]
        )
        mat = loadmat(str(path), variable_names=var_names, squeeze_me=True)
        data: dict[str, Any] = {}
        for col, rc in self.column_map.items():
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
            data[col.mr_name] = arr.astype(np.float64)
        df = pl.DataFrame(data)
        return df.lazy(), []

    def read(
        self,
        path: str | Path,
        *,
        lazy: bool = False,
        column_map: dict[str, str] | None = None,
    ) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
        path = Path(path)
        lf, _ = self._parse(path)
        exprs: list[pl.Expr] = []
        meta: dict = {"source": f"mat:{path.stem}", "columns": {}}
        for col, rc in self.column_map.items():
            if not self.include_optional and not col.required:
                continue
            expr = pl.col(col.mr_name)
            if rc.offset != 0.0:
                expr = expr + rc.offset
            if rc.scale != 1.0:
                expr = expr * rc.scale
            if col.dtype == "int":
                expr = expr.cast(pl.Int64, strict=False)
            exprs.append(expr.alias(col.mr_name))
            meta["columns"][col.mr_name] = {
                "source_header": rc.source_header,
                "source_unit": rc.bdf_unit,
                "bdf_unit": rc.bdf_unit,
                "scale": rc.scale,
                "offset": rc.offset,
                "datetime_fmt": None,
            }
        if self.extra_columns:
            for src, out in self.extra_columns.items():
                if src in lf.collect_schema().names():
                    exprs.append(pl.col(src).alias(out))
        result_lf = lf.select(exprs)
        if lazy:
            return result_lf, meta
        return result_lf.collect(), meta


__all__ = ["BaseReader", "CSVReader", "ExcelReader", "MATReader"]
