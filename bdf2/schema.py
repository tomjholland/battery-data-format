"""Typed BDF schema: BDFColumn enum, Syn/DateTimeSyn, FieldSpec, Normalizer."""

from __future__ import annotations

import contextlib
import re
from enum import Enum
from typing import Annotated, Any, Literal

import pint
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

_ureg = pint.UnitRegistry()
for _alias, _canonical in [
    ("degc", "degC"),
    ("degreec", "degC"),
    ("\xf8c", "degC"),
]:
    with contextlib.suppress(Exception):
        _ureg.define(f"{_alias} = {_canonical}")


class BDFColumn(Enum):
    """Canonical BDF columns; each member carries unit/required/label/iri metadata."""

    TEST_TIME_SECOND = (
        "test_time_second", "s", True,
        "Test Time / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#test_time_second",
    )
    VOLTAGE_VOLT = (
        "voltage_volt", "V", True,
        "Voltage / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt",
    )
    CURRENT_AMPERE = (
        "current_ampere", "A", True,
        "Current / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#current_ampere",
    )
    UNIX_TIME_SECOND = (
        "unix_time_second", "s", False,
        "Unix Time / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#unix_time_second",
    )
    CYCLE_COUNT = (
        "cycle_count", "1", False,
        "Cycle Count / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cycle_count",
    )
    STEP_COUNT = (
        "step_count", "1", False,
        "Step Count / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_count",
    )
    AMBIENT_TEMPERATURE_CELSIUS = (
        "ambient_temperature_celsius", "degC", False,
        "Ambient Temperature / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_temperature_celsius",
    )
    STEP_INDEX = (
        "step_index", "1", False,
        "Step Index / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_index",
    )
    STEP_TIME_SECOND = (
        "step_time_second", "s", False,
        "Step Time / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_time_second",
    )
    CHARGING_CAPACITY_AH = (
        "charging_capacity_ah", "Ah", False,
        "Charging Capacity / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_capacity_ah",
    )
    DISCHARGING_CAPACITY_AH = (
        "discharging_capacity_ah", "Ah", False,
        "Discharging Capacity / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_capacity_ah",
    )
    STEP_CAPACITY_AH = (
        "step_capacity_ah", "Ah", False,
        "Step Capacity / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_capacity_ah",
    )
    NET_CAPACITY_AH = (
        "net_capacity_ah", "Ah", False,
        "Net Capacity / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_capacity_ah",
    )
    CUMULATIVE_CAPACITY_AH = (
        "cumulative_capacity_ah", "Ah", False,
        "Cumulative Capacity / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_capacity_ah",
    )
    CHARGING_ENERGY_WH = (
        "charging_energy_wh", "Wh", False,
        "Charging Energy / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#charging_energy_wh",
    )
    DISCHARGING_ENERGY_WH = (
        "discharging_energy_wh", "Wh", False,
        "Discharging Energy / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#discharging_energy_wh",
    )
    STEP_ENERGY_WH = (
        "step_energy_wh", "Wh", False,
        "Step Energy / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#step_energy_wh",
    )
    NET_ENERGY_WH = (
        "net_energy_wh", "Wh", False,
        "Net Energy / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#net_energy_wh",
    )
    CUMULATIVE_ENERGY_WH = (
        "cumulative_energy_wh", "Wh", False,
        "Cumulative Energy / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#cumulative_energy_wh",
    )
    POWER_WATT = (
        "power_watt", "W", False,
        "Power / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#power_watt",
    )
    INTERNAL_RESISTANCE_OHM = (
        "internal_resistance_ohm", "ohm", False,
        "Internal Resistance / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#internal_resistance_ohm",
    )
    AMBIENT_PRESSURE_PA = (
        "ambient_pressure_pa", "Pa", False,
        "Ambient Pressure / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#ambient_pressure_pa",
    )
    APPLIED_PRESSURE_PA = (
        "applied_pressure_pa", "Pa", False,
        "Applied Pressure / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#applied_pressure_pa",
    )
    TEMPERATURE_T1_CELSIUS = (
        "temperature_t1_celsius", "degC", False,
        "Surface Temperature T1 / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t1_celsius",
    )
    TEMPERATURE_T2_CELSIUS = (
        "temperature_t2_celsius", "degC", False,
        "Surface Temperature T2 / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t2_celsius",
    )
    TEMPERATURE_T3_CELSIUS = (
        "temperature_t3_celsius", "degC", False,
        "Surface Temperature T3 / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t3_celsius",
    )
    TEMPERATURE_T4_CELSIUS = (
        "temperature_t4_celsius", "degC", False,
        "Surface Temperature T4 / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t4_celsius",
    )
    TEMPERATURE_T5_CELSIUS = (
        "temperature_t5_celsius", "degC", False,
        "Surface Temperature T5 / {unit}",
        "https://w3id.org/battery-data-alliance/ontology/battery-data-format#temperature_t5_celsius",
    )

    def __init__(
        self,
        mr_name: str,
        unit: str,
        required: bool,
        label_template: str,
        iri: str,
    ) -> None:
        self.mr_name = mr_name
        self.unit = unit
        self.required = required
        self.label_template = label_template
        self.iri = iri

    @property
    def label(self) -> str:
        return self.label_template.format(unit=self.unit)

    @property
    def dtype(self) -> str:
        if self.unit == "1":
            return "int"
        return "float"


for _col in BDFColumn:
    try:
        _ureg.parse_expression(_col.unit)
    except Exception as _exc:
        raise AssertionError(
            f"BDFColumn.{_col.name}: unit {_col.unit!r} not parseable by pint: {_exc}"
        ) from _exc


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


class FieldSpec(BaseModel):
    """A list of synonyms for a single BDF column on one source."""

    model_config = ConfigDict(frozen=True)

    synonyms: list[SynUnion]


def column(*synonyms: str | Syn | DateTimeSyn) -> FieldSpec:
    """Build a FieldSpec from string exemplars and/or Syn/DateTimeSyn instances."""
    parsed: list[Syn | DateTimeSyn] = []
    for s in synonyms:
        if isinstance(s, str):
            parsed.append(Syn.parse(s))
        elif isinstance(s, (Syn, DateTimeSyn)):
            parsed.append(s)
        else:
            raise TypeError(f"column() expects str | Syn | DateTimeSyn, got {type(s).__name__}")
    return FieldSpec(synonyms=parsed)


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


class Normalizer(BaseModel):
    """A single source: magic strings, extensions, metadata patterns, and column synonyms."""

    model_config = ConfigDict(frozen=True)

    id: str
    magic: tuple[str, ...] = ()
    exts: tuple[str, ...] = ()
    metadata_patterns: dict[str, str] = Field(default_factory=dict)
    datetime_formats: tuple[str, ...] = ()
    columns: dict[BDFColumn, FieldSpec]

    @field_validator("columns", mode="before")
    @classmethod
    def _coerce_column_keys(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        out: dict[Any, Any] = {}
        for k, val in v.items():
            if isinstance(k, BDFColumn):
                out[k] = val
            elif isinstance(k, str) and k in BDFColumn.__members__:
                out[BDFColumn[k]] = val
            else:
                out[k] = val
        return out

    @field_serializer("columns")
    def _ser_columns(self, v: dict[BDFColumn, FieldSpec]) -> dict[str, dict]:
        return {col.name: spec.model_dump() for col, spec in v.items()}

    @model_validator(mode="after")
    def _check_columns(self) -> "Normalizer":
        if not self.columns:
            raise ValueError(f"Normalizer {self.id!r}: columns dict must not be empty")
        for col, spec in self.columns.items():
            for syn in spec.synonyms:
                if isinstance(syn, DateTimeSyn):
                    continue
                if syn.unit is None:
                    continue
                factor = _pint_scale(syn.unit, col.unit)
                if factor is None:
                    raise ValueError(
                        f"Normalizer {self.id!r}: {col.name} synonym {syn.exemplar!r} "
                        f"unit {syn.unit!r} is not pint-compatible with {col.unit!r}"
                    )
        return self

    def resolve(self, headers: list[str]) -> dict[BDFColumn, ResolvedColumn]:
        """Match `headers` to BDF columns, returning the resolved mapping."""
        out: dict[BDFColumn, ResolvedColumn] = {}
        claimed: set[str] = set()
        probes = {h: h.strip().lstrip("~").strip() for h in headers}
        styled = {h: _parse_styled(probes[h]) for h in headers}
        for col, spec in self.columns.items():
            for header in headers:
                if header in claimed:
                    continue
                matched = self._match_header(header, probes[header], styled[header], col, spec)
                if matched is not None:
                    out[col] = matched
                    claimed.add(header)
                    break
        return out

    def _match_header(
        self,
        header: str,
        probe: str,
        styled: list[tuple[Style, str, str | None]],
        col: BDFColumn,
        spec: FieldSpec,
    ) -> ResolvedColumn | None:
        for syn in spec.synonyms:
            if isinstance(syn, DateTimeSyn):
                if probe.lower() == syn.exemplar.strip().lower():
                    return ResolvedColumn(
                        source_header=header,
                        bdf_unit=col.unit,
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
                    factor = _pint_scale(unit, col.unit)
                    if factor is None:
                        continue
                return ResolvedColumn(
                    source_header=header,
                    bdf_unit=col.unit,
                    scale=factor,
                    offset=0.0,
                    datetime_fmt=None,
                )
        return None

    def score(self, headers: list[str]) -> int:
        return len(self.resolve(headers))

    def match_magic(self, head: bytes) -> bool:
        if not self.magic:
            return False
        try:
            text = head.decode("utf-8", errors="replace").lower()
        except Exception:
            return False
        return any(m.lower() in text for m in self.magic)


__all__ = [
    "BDFColumn",
    "Style",
    "Syn",
    "DateTimeSyn",
    "SynUnion",
    "FieldSpec",
    "column",
    "ResolvedColumn",
    "Normalizer",
]
