"""Typed BDF schema: Syn/DateTimeSyn, ResolvedColumn, Normalizer, Source, MetadataParser."""

from __future__ import annotations

import contextlib
import logging
import re
import warnings
from enum import Enum
from typing import Annotated, Any, Iterator, Literal

import pint
import polars as pl
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    PrivateAttr,
    model_validator,
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


class Style(str, Enum):
    BRACKETS = "BRACKETS"
    PARENS = "PARENS"
    SLASH = "SLASH"
    NONE = "NONE"


_BRACKETS_RE = re.compile(r"^\s*(.+?)\s*\[([^\]]+)\]\s*$")
_PARENS_RE = re.compile(r"^\s*(.+?)\s*\(([^)]+)\)\s*$")
_SLASH_RE = re.compile(r"^\s*(.+?)\s*/\s*(.+?)\s*$")


def _parse_styled(header: str) -> list[tuple[Style, str, str | None]]:
    """Return all (style, qty, unit) tuples that match `header`. NONE always last."""
    out: list[tuple[Style, str, str | None]] = []
    m = _BRACKETS_RE.match(header)
    if m:
        out.append((Style.BRACKETS, m.group(1), m.group(2)))
    m = _PARENS_RE.match(header)
    if m:
        out.append((Style.PARENS, m.group(1), m.group(2)))
    m = _SLASH_RE.match(header)
    if m:
        out.append((Style.SLASH, m.group(1), m.group(2)))
    out.append((Style.NONE, header.strip(), None))
    return out


class Syn(BaseModel):
    """A numeric column synonym declared by exemplar header."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["numeric"] = "numeric"
    exemplar: str
    qty: str
    style: Style
    unit: str | None

    @classmethod
    def parse(cls, exemplar: str) -> "Syn":
        m = _BRACKETS_RE.match(exemplar)
        if m:
            return cls(exemplar=exemplar, qty=m.group(1).strip(), style=Style.BRACKETS, unit=m.group(2).strip())
        m = _PARENS_RE.match(exemplar)
        if m:
            return cls(exemplar=exemplar, qty=m.group(1).strip(), style=Style.PARENS, unit=m.group(2).strip())
        m = _SLASH_RE.match(exemplar)
        if m:
            return cls(exemplar=exemplar, qty=m.group(1).strip(), style=Style.SLASH, unit=m.group(2).strip())
        return cls(exemplar=exemplar, qty=exemplar.strip(), style=Style.NONE, unit=None)


class DateTimeSyn(BaseModel):
    """A datetime column synonym matched by exemplar equality with a strftime format."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["datetime"] = "datetime"
    exemplar: str
    qty: str = ""
    fmt: str

    @model_validator(mode="after")
    def _fill_qty(self) -> "DateTimeSyn":
        if not self.qty:
            object.__setattr__(self, "qty", self.exemplar)
        return self


SynUnion = Annotated[Syn | DateTimeSyn, Discriminator("kind")]


class ResolvedColumn(BaseModel):
    """Resolved mapping of one source header to one BDF column."""

    model_config = ConfigDict(frozen=True)

    source_header: str
    bdf_unit: str | None = None
    scale: float = 1.0
    offset: float = 0.0
    datetime_fmt: str | None = None


_UNIT_FIXUPS = {
    "°c": "degC",
    "°C": "degC",
    "\xf8c": "degC",
    "\xf8C": "degC",
}


def _norm_unit(u: str) -> str:
    s = u.strip()
    for k, v in _UNIT_FIXUPS.items():
        if k in s:
            s = s.replace(k, v)
    return s


def _pint_scale(src_unit: str | None, dst_unit: str) -> float | None:
    """Return pint conversion factor src→dst, or None when incompatible/unparseable."""
    if src_unit is None or dst_unit in ("1", "", None):
        if (src_unit is None or src_unit.strip() in ("", "1")) and dst_unit in ("1", "", None):
            return 1.0
        return None
    s = _norm_unit(src_unit)
    t = _norm_unit(dst_unit)
    if s.lower() == t.lower():
        return 1.0
    try:
        src_q = _ureg.Quantity(1, s)
        tgt_q = _ureg.Quantity(1, t)
        if src_q.dimensionality != tgt_q.dimensionality:
            return None
        converted = src_q.to(tgt_q.units)
        return round(float(converted.magnitude), 15)
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
    if rc.datetime_fmt is not None:
        expr = (
            pl.col(src)
            .str.to_datetime(rc.datetime_fmt, strict=False)
            .dt.timestamp("ms")
            .cast(pl.Float64) / 1000.0
        )
        return expr.alias(mr_name)
    expr = pl.col(src)
    if decimal != "." and schema.get(src) in (pl.String, pl.Utf8):
        expr = expr.str.replace_all(decimal, ".", literal=True)
    expr = expr.cast(pl.Float64, strict=False)
    if rc.offset != 0.0:
        expr = expr + rc.offset
    if rc.scale != 1.0:
        expr = expr * rc.scale
    if _col_dtype(mr_name) == "int":
        expr = expr.cast(pl.Int64, strict=False)
    return expr.alias(mr_name)


def _resolved_from_column_map_value(mr_name: str, src_header: str) -> ResolvedColumn:
    src_unit: str | None = None
    for style, _qty, unit in _parse_styled(src_header):
        if style != Style.NONE and unit is not None:
            src_unit = unit
            break
    bdf_unit = _col_unit(mr_name)
    if src_unit is None:
        scale = 1.0
    else:
        factor = _pint_scale(src_unit, bdf_unit)
        if factor is None:
            warnings.warn(
                f"column_map: unit {src_unit!r} on {src_header!r} not compatible "
                f"with {bdf_unit!r} for {mr_name}; using scale=1.0",
                UserWarning,
                stacklevel=4,
            )
            scale = 1.0
        else:
            scale = factor
    return ResolvedColumn(
        source_header=src_header,
        bdf_unit=bdf_unit,
        scale=scale,
        offset=0.0,
        datetime_fmt=None,
    )


def _match_header(
    header: str,
    probe: str,
    styled: list[tuple[Style, str, str | None]],
    bdf_unit: str,
    synonyms: list[Syn | DateTimeSyn],
) -> ResolvedColumn | None:
    for syn in synonyms:
        if isinstance(syn, DateTimeSyn):
            if probe.lower() == syn.exemplar.strip().lower():
                return ResolvedColumn(
                    source_header=header,
                    bdf_unit=bdf_unit,
                    scale=1.0,
                    offset=0.0,
                    datetime_fmt=syn.fmt,
                )
            continue
        for style, qty, unit in styled:
            if style != syn.style:
                continue
            if qty.strip().lower() != syn.qty.strip().lower():
                continue
            if syn.style == Style.NONE:
                factor = 1.0
            else:
                factor = _pint_scale(unit, bdf_unit)
                if factor is None:
                    continue
            return ResolvedColumn(
                source_header=header,
                bdf_unit=bdf_unit,
                scale=factor,
                offset=0.0,
                datetime_fmt=None,
            )
    return None


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

    @model_validator(mode="after")
    def _check_syn_units(self) -> "Normalizer":
        for mr_name in type(self).model_fields:
            spec = getattr(self, mr_name)
            if spec is None or isinstance(spec, ResolvedColumn):
                continue
            unit = _col_unit(mr_name)
            for syn in spec:
                if isinstance(syn, DateTimeSyn):
                    continue
                if syn.unit is None:
                    continue
                factor = _pint_scale(syn.unit, unit)
                if factor is None:
                    raise ValueError(
                        f"Normalizer: {mr_name} synonym {syn.exemplar!r} "
                        f"unit {syn.unit!r} is not pint-compatible with {unit!r}"
                    )
        return self

    def __iter__(self) -> Iterator[tuple[str, list[Syn | DateTimeSyn] | ResolvedColumn]]:  # type: ignore[override]
        for mr_name in type(self).model_fields:
            val = getattr(self, mr_name)
            if val is not None:
                yield mr_name, val

    def score(self, headers: list[str]) -> int:
        """Count how many headers match via synonym scanning (ResolvedColumn fields skipped)."""
        probes = {h: h.strip().lstrip("~").strip() for h in headers}
        styled_map = {h: _parse_styled(probes[h]) for h in headers}
        claimed: set[str] = set()
        count = 0
        for mr_name, spec in self:
            if isinstance(spec, ResolvedColumn):
                continue
            unit = _col_unit(mr_name)
            for header in headers:
                if header in claimed:
                    continue
                if _match_header(header, probes[header], styled_map[header], unit, spec) is not None:
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
        styled_map = {h: _parse_styled(probes[h]) for h in headers}

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
                    matched = _match_header(header, probes[header], styled_map[header], unit, spec)
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

        for mr_name, rc in resolved.items():
            if rc.source_header not in headers:
                _logger.info(
                    "normalize: source header %r not present in DataFrame; skipping",
                    rc.source_header,
                )
                continue
            src_unit: str | None = None
            if rc.datetime_fmt is None:
                for style, _qty, unit in _parse_styled(rc.source_header):
                    if style != Style.NONE and unit is not None:
                        src_unit = _norm_unit(unit)
                        break
            columns_meta[mr_name] = {
                "source_header": rc.source_header,
                "source_unit": src_unit,
                "bdf_unit": rc.bdf_unit,
                "scale": rc.scale,
                "offset": rc.offset,
                "datetime_fmt": rc.datetime_fmt,
            }
            exprs.append(_build_expr(mr_name, rc, dict(schema), decimal))

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
    datetime_formats: tuple[str, ...] = ()
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
    "Style",
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
    "_parse_styled",
    "_match_header",
    "_build_expr",
    "_resolved_from_column_map_value",
]
