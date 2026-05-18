"""Typed BDF schema: Syn, DateTimeSyn, ResolvedColumn, Normalizer, Source, MetadataParser."""

from __future__ import annotations

import contextlib
import functools
import logging
import re
import warnings
from typing import Any, Iterator

import pint
import polars as pl
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    RootModel,
)

from bdf.normalize.spec import COLUMNS as _SPEC_COLUMNS

_ureg = pint.UnitRegistry()
for _alias, _canonical in [
    ("degc", "degC"),
    ("degreec", "degC"),
    ("\xf8c", "degC"),
]:
    with contextlib.suppress(Exception):
        _ureg.define(f"{_alias} = {_canonical}")

_logger = logging.getLogger(__name__)


def _col_unit(mr_name: str) -> str:
    return str(_SPEC_COLUMNS[mr_name]["unit"])


def _col_required(mr_name: str) -> bool:
    return bool(_SPEC_COLUMNS[mr_name].get("required", False))


def _col_dtype(mr_name: str) -> str:
    return "int" if _col_unit(mr_name) == "1" else "float"


_BRACKETS_RE = re.compile(r"^\s*(.+?)\s*\[([^\]]+)\]\s*$")
_PARENS_RE = re.compile(r"^\s*(.+?)\s*\(([^)]+)\)\s*$")
_SLASH_RE = re.compile(r"^\s*(.+?)\s*/\s*(.+?)\s*$")

_UNIT_CAPTURE = r"([A-Za-z0-9./]+)"


def _extract_unit(header: str) -> str | None:
    """Extract unit string from bracket/parens/slash-style header."""
    m = _BRACKETS_RE.match(header) or _PARENS_RE.match(header) or _SLASH_RE.match(header)
    return m.group(2).strip() if m else None


class Syn(RootModel[str]):
    """A numeric column synonym declared by exemplar header."""

    model_config = ConfigDict(frozen=True)

    @property
    def exemplar(self) -> str:
        return self.root

    def match(self, header: str, bdf_unit: str) -> tuple[float, float] | None:
        """Return (scale, offset) on match, None on no match or incompatible units."""
        if "{unit}" in self.root:
            parts = self.root.split("{unit}")
            pattern = _UNIT_CAPTURE.join(re.escape(p) for p in parts)
            m = re.fullmatch(pattern, header, re.IGNORECASE)
            if m is None:
                return None
            return _pint_scale(m.group(1), bdf_unit)
        return (1.0, 0.0) if self.root.strip().lower() == header.strip().lower() else None

    def exact_match(self, header: str) -> bool:
        return self.root.strip().lower() == header.strip().lower()


class DateTimeSyn(BaseModel):
    """A datetime column synonym: one header synonym plus ordered format strings to try."""

    model_config = ConfigDict(frozen=True)

    syn: Syn
    fmts: tuple[str, ...]


SynUnion = Syn | DateTimeSyn


class ResolvedColumn(BaseModel):
    """Resolved mapping of one source header to one BDF column."""

    model_config = ConfigDict(frozen=True)

    source_header: str
    bdf_unit: str | None = None
    scale: float = 1.0
    offset: float = 0.0
    datetime_fmts: tuple[str, ...] = ()

    @classmethod
    def from_synonyms(
        cls,
        header: str,
        probe: str,
        bdf_unit: str,
        synonyms: list[SynUnion],
    ) -> ResolvedColumn | None:
        for syn in synonyms:
            if isinstance(syn, DateTimeSyn):
                if syn.syn.exact_match(probe):
                    return cls(
                        source_header=header,
                        bdf_unit=bdf_unit,
                        datetime_fmts=syn.fmts,
                    )
            else:
                result = syn.match(probe, bdf_unit)
                if result is not None:
                    scale, offset = result
                    return cls(
                        source_header=header,
                        bdf_unit=bdf_unit,
                        scale=scale,
                        offset=offset,
                    )
        return None


_UNIT_FIXUPS = {
    "°c": "degC",
    "°C": "degC",
    "\xf8c": "degC",
    "\xf8C": "degC",
}


@functools.lru_cache(maxsize=256)
def _norm_unit(u: str) -> str:
    s = u.strip()
    for k, v in _UNIT_FIXUPS.items():
        if k in s:
            s = s.replace(k, v)
    return s


@functools.lru_cache(maxsize=256)
def _pint_scale(src_unit: str | None, dst_unit: str) -> tuple[float, float] | None:
    """Return (scale, offset) for src→dst conversion, or None when incompatible/unparseable.

    Handles affine conversions (e.g. °F→°C) via two-point probe: offset = f(0), scale = f(1)-f(0).
    """
    if src_unit is None or dst_unit in ("1", "", None):
        if (src_unit is None or src_unit.strip() in ("", "1")) and dst_unit in ("1", "", None):
            return (1.0, 0.0)
        return None
    s = _norm_unit(src_unit)
    t = _norm_unit(dst_unit)
    if s.lower() == t.lower():
        return (1.0, 0.0)
    try:
        tgt_units = _ureg.Quantity(1, t).units
        if _ureg.Quantity(1, s).dimensionality != _ureg.Quantity(1, t).dimensionality:
            return None
        at_zero = float(_ureg.Quantity(0, s).to(tgt_units).magnitude)
        at_one = float(_ureg.Quantity(1, s).to(tgt_units).magnitude)
        scale = round(at_one - at_zero, 15)
        offset = round(at_zero, 15)
        return (scale, offset)
    except Exception:
        return None


class MetadataParser(BaseModel):
    """Fixed-field model for BDF-approved metadata extraction from preamble lines."""

    model_config = ConfigDict(frozen=True)

    start_time: str | None = None

    _compiled: dict[str, re.Pattern[str]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        for field_name in type(self).model_fields:
            pattern = getattr(self, field_name)
            if pattern is not None:
                self._compiled[field_name] = re.compile(pattern, re.IGNORECASE)

    def parse(self, lines: list[str]) -> dict[str, str]:
        """Apply each non-None pattern to lines; return first match per key."""
        result: dict[str, str] = {}
        for field_name, rx in self._compiled.items():
            for line in lines:
                m = rx.search(line)
                if m:
                    result[field_name] = m.group(1).strip()
                    break
        return result


def _sniff_decimal(df: pl.DataFrame | pl.LazyFrame) -> str:
    """Return ',' if comma-decimal strings dominate string columns, else '.'."""
    sample = df.head(1000).collect() if isinstance(df, pl.LazyFrame) else df.head(1000)
    comma = dot = 0
    for col in sample.columns:
        if sample[col].dtype in (pl.String, pl.Utf8):
            comma += int(sample[col].str.count_matches(r"\d+,\d+").sum())
            dot += int(sample[col].str.count_matches(r"\d+\.\d+").sum())
    return "," if comma > dot else "."


def _build_expr(
    mr_name: str,
    rc: ResolvedColumn,
    schema: dict,
    decimal: str,
) -> pl.Expr:
    src = rc.source_header
    if rc.datetime_fmts:
        candidates = [
            pl.col(src).str.to_datetime(fmt, strict=False)
            for fmt in rc.datetime_fmts
        ]
        expr = (
            (pl.coalesce(candidates) if len(candidates) > 1 else candidates[0])
            .dt.timestamp("ms")
            .cast(pl.Float64) / 1000.0
        )
        return expr.alias(mr_name)
    expr = pl.col(src)
    if decimal != "." and schema.get(src) in (pl.String, pl.Utf8):
        expr = expr.str.replace_all(decimal, ".", literal=True)
    expr = expr.cast(pl.Float64, strict=False)
    if rc.scale != 1.0:
        expr = expr * rc.scale
    if rc.offset != 0.0:
        expr = expr + rc.offset
    if _col_dtype(mr_name) == "int":
        expr = expr.cast(pl.Int64, strict=False)
    return expr.alias(mr_name)


def _resolved_from_column_map_value(mr_name: str, src_header: str) -> ResolvedColumn:
    src_unit = _extract_unit(src_header)
    bdf_unit = _col_unit(mr_name)
    if src_unit is None:
        scale, offset = 1.0, 0.0
    else:
        result = _pint_scale(src_unit, bdf_unit)
        if result is None:
            warnings.warn(
                f"column_map: unit {src_unit!r} on {src_header!r} not compatible "
                f"with {bdf_unit!r} for {mr_name}; using scale=1.0",
                UserWarning,
                stacklevel=4,
            )
            scale, offset = 1.0, 0.0
        else:
            scale, offset = result
    return ResolvedColumn(
        source_header=src_header,
        bdf_unit=bdf_unit,
        scale=scale,
        offset=offset,
    )


class Normalizer(BaseModel):
    """Column-mapping model: one optional field per BDF mr_name.

    Fields accept ``list[Syn | DateTimeSyn]`` (synonym-based, for CSV/Excel) or
    ``ResolvedColumn`` (direct, for MAT). Iterating yields ``(mr_name, spec)``
    for non-None fields in declaration order.
    """

    model_config = ConfigDict(frozen=True)

    test_time_second: list[SynUnion] | ResolvedColumn | None = None
    voltage_volt: list[SynUnion] | ResolvedColumn | None = None
    current_ampere: list[SynUnion] | ResolvedColumn | None = None
    unix_time_second: list[SynUnion] | ResolvedColumn | None = None
    cycle_count: list[SynUnion] | ResolvedColumn | None = None
    step_count: list[SynUnion] | ResolvedColumn | None = None
    ambient_temperature_celsius: list[SynUnion] | ResolvedColumn | None = None
    step_index: list[SynUnion] | ResolvedColumn | None = None
    step_time_second: list[SynUnion] | ResolvedColumn | None = None
    charging_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    discharging_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    step_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    net_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    cumulative_capacity_ah: list[SynUnion] | ResolvedColumn | None = None
    charging_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    discharging_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    step_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    net_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    cumulative_energy_wh: list[SynUnion] | ResolvedColumn | None = None
    power_watt: list[SynUnion] | ResolvedColumn | None = None
    internal_resistance_ohm: list[SynUnion] | ResolvedColumn | None = None
    ambient_pressure_pa: list[SynUnion] | ResolvedColumn | None = None
    applied_pressure_pa: list[SynUnion] | ResolvedColumn | None = None
    temperature_t1_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t2_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t3_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t4_celsius: list[SynUnion] | ResolvedColumn | None = None
    temperature_t5_celsius: list[SynUnion] | ResolvedColumn | None = None

    def __iter__(self) -> Iterator[tuple[str, list[SynUnion] | ResolvedColumn]]:  # type: ignore[override]
        for mr_name in type(self).model_fields:
            val = getattr(self, mr_name)
            if val is not None:
                yield mr_name, val

    def score(self, headers: list[str]) -> int:
        """Count how many headers match via synonym scanning (ResolvedColumn fields skipped)."""
        probes = {h: h.strip().lstrip("~").strip() for h in headers}
        claimed: set[str] = set()
        count = 0
        for mr_name, spec in self:
            if isinstance(spec, ResolvedColumn):
                continue
            unit = _col_unit(mr_name)
            for header in headers:
                if header in claimed:
                    continue
                if ResolvedColumn.from_synonyms(header, probes[header], unit, spec) is not None:
                    claimed.add(header)
                    count += 1
                    break
        return count

    def normalize(
        self,
        df: pl.DataFrame | pl.LazyFrame,
        *,
        include_optional: bool = True,
        column_map: dict[str, str] | None = None,
        extra_columns: dict[str, str] | None = None,
        decimal: str | None = None,
    ) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
        """Resolve headers → BDF columns, apply unit conversion, return (df_out, columns_meta)."""
        schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
        headers = list(schema.names())

        probes = {h: h.strip().lstrip("~").strip() for h in headers}

        resolved: dict[str, ResolvedColumn] = {}
        claimed: set[str] = set()

        for mr_name, spec in self:
            unit = _col_unit(mr_name)
            if isinstance(spec, ResolvedColumn):
                resolved[mr_name] = spec
                if spec.source_header in headers:
                    claimed.add(spec.source_header)
            else:
                for header in headers:
                    if header in claimed:
                        continue
                    matched = ResolvedColumn.from_synonyms(header, probes[header], unit, spec)
                    if matched is not None:
                        resolved[mr_name] = matched
                        claimed.add(header)
                        break

        if column_map:
            for mr_name, src_header in column_map.items():
                if mr_name not in _SPEC_COLUMNS:
                    raise ValueError(f"column_map key {mr_name!r} is not a valid BDF mr_name")
                resolved[mr_name] = _resolved_from_column_map_value(mr_name, src_header)

        if not include_optional:
            resolved = {mr: r for mr, r in resolved.items() if _col_required(mr)}

        if decimal is None:
            decimal = _sniff_decimal(df)

        columns_meta: dict = {}
        exprs: list[pl.Expr] = []
        schema_dict = dict(schema)

        for mr_name, rc in resolved.items():
            if rc.source_header not in headers:
                _logger.info(
                    "normalize: source header %r not present in DataFrame; skipping",
                    rc.source_header,
                )
                continue
            src_unit: str | None = None
            if not rc.datetime_fmts:
                raw_unit = _extract_unit(rc.source_header)
                if raw_unit is not None:
                    src_unit = _norm_unit(raw_unit)
            columns_meta[mr_name] = {
                "source_header": rc.source_header,
                "source_unit": src_unit,
                "bdf_unit": rc.bdf_unit,
                "scale": rc.scale,
                "offset": rc.offset,
                "datetime_fmts": rc.datetime_fmts,
            }
            exprs.append(_build_expr(mr_name, rc, schema_dict, decimal))

        if extra_columns:
            for src, out in extra_columns.items():
                if src not in headers:
                    warnings.warn(
                        f"extra_columns source {src!r} not in DataFrame columns; skipping",
                        UserWarning,
                        stacklevel=3,
                    )
                    continue
                exprs.append(pl.col(src).alias(out))

        if not exprs:
            return df, columns_meta

        return df.select(exprs), columns_meta


class Source(BaseModel):
    """A single battery cycler source: identity (id, magic, extensions), metadata parser,
    and column-mapping normalizer."""

    model_config = ConfigDict(frozen=True)

    id: str
    magic: tuple[str, ...] = ()
    exts: tuple[str, ...] = ()
    metadata: MetadataParser = Field(default_factory=MetadataParser)
    normalizer: Normalizer

    def score(self, headers: list[str]) -> int:
        return self.normalizer.score(headers)

    def match_magic(self, head: bytes) -> bool:
        if not self.magic:
            return False
        try:
            text = head.decode("utf-8", errors="replace").lower()
        except Exception:
            return False
        return any(m.lower() in text for m in self.magic)


__all__ = [
    "Syn",
    "DateTimeSyn",
    "SynUnion",
    "ResolvedColumn",
    "MetadataParser",
    "Normalizer",
    "Source",
    "_SPEC_COLUMNS",
    "_col_unit",
    "_col_required",
    "_col_dtype",
    "_sniff_decimal",
    "_pint_scale",
    "_norm_unit",
    "_extract_unit",
    "_build_expr",
    "_resolved_from_column_map_value",
]
