"""Ask for any BDF column by name, whether or not the data records it.

A request names a BDF quantity in either style, optionally in a unit other
than its canonical one:

- preferred label: ``"Voltage / V"``, ``"Voltage / mV"``, ``"Net Capacity / mAh"``
- machine-readable name: ``"voltage_volt"``, ``"voltage_mV"``, ``"net_capacity_mah"``

Resolution tries, in order: a column of that exact name; a recorded column of
the same quantity, in any unit and either label style; then the recipes in
:data:`bdf.recipes.RECIPES`, preferring a recipe whose inputs are all recorded
over one that needs inputs derived first. The result is converted to the
requested unit.

Unit conversion only happens when the request names a unit. On a
machine-readable name the unit follows the quantity's stem (the name without
its canonical unit suffix) and is any of:

- the canonical suffix itself: ``voltage_volt``, ``net_capacity_ah``
- a case-sensitive unit symbol pint or UCUM understands: ``voltage_mV``,
  ``test_time_h``, ``ambient_temperature_K``, ``applied_pressure_MPa``
- a pint long unit name: ``voltage_millivolt``, ``test_time_hour``
- a lowercase SI prefix on the canonical suffix: ``net_capacity_mah``,
  ``net_energy_kwh``, ``applied_pressure_kpa``

Symbols are case-sensitive because SI prefixes are (``mW`` is milliwatt,
``MW`` megawatt), and because lowercase symbols collide with other units in
pint (``ma`` is a milliyear, ``ah`` an attohour). Each unit is checked against
the quantity's dimension, so such a collision raises instead of converting.

:func:`resolve` works from column names alone and returns Polars expressions,
so it serves any frame type. :func:`select` and :func:`with_columns` apply
those expressions to a pandas DataFrame, polars DataFrame, or polars
LazyFrame, and return the same kind. Importing this module also registers the
``bdf`` namespace on polars frames: ``df.bdf.with_columns("power_watt")``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl

from bdf import _polars_namespace  # noqa: F401  (registers the `bdf` namespace)
from bdf._df_compat import _classify_df, _from_polars_lazy, _to_polars_lazy
from bdf._errors import BDFColumnError
from bdf.recipes import RECIPES, Recipe
from bdf.spec import COLUMN_ONTOLOGY, Quantity, _slugify, get_unit_conversion, parse_label

if TYPE_CHECKING:
    from bdf._df_compat import AnyDF

__all__ = ["ColumnName", "can_resolve", "parse_name", "resolve", "select", "with_columns"]

_DIMENSIONLESS = (None, "1")
_LOWERCASE_PREFIXES = ("m", "k", "u", "n", "p", "c")
_TEMPERATURE_UNITS = frozenset({"degC", "K", "degF"})


@dataclass(frozen=True)
class ColumnName:
    """A column name parsed into its BDF quantity and the unit it names.

    Attributes:
        quantity: Machine-readable name of the quantity, e.g. ``"voltage_volt"``.
        unit: The unit the name asks for, e.g. ``"mV"``; the canonical unit
            when the name gives none, and ``None`` for unitless quantities.

    Examples:
        >>> parse_name("Voltage / mV")
        ColumnName(quantity='voltage_volt', unit='mV')
        >>> parse_name("net_capacity_mah")
        ColumnName(quantity='net_capacity_ah', unit='mAh')
    """

    quantity: str
    unit: str | None


# --------- Name parsing ----------


@dataclass(frozen=True)
class _NameIndex:
    exact: dict[str, str]
    """Preferred label or machine-readable name -> mr_name."""
    by_base: dict[str, str]
    """Lowercased preferred-label base (``"voltage"``) -> mr_name."""
    stems: tuple[tuple[str, str, str], ...]
    """``(stem, canonical_suffix, mr_name)``, longest stem first."""


_index_cache: dict[int, _NameIndex] = {}


def _name_index() -> _NameIndex:
    """Lookup tables for the loaded ontology, rebuilt if it is reloaded."""
    key = id(COLUMN_ONTOLOGY._quantities)
    cached = _index_cache.get(key)
    if cached is not None:
        return cached
    exact: dict[str, str] = {}
    by_base: dict[str, str] = {}
    stems: list[tuple[str, str, str]] = []
    for mr_name, q in COLUMN_ONTOLOGY:
        if q.deprecated:
            continue
        exact.setdefault(q.formatted_label, mr_name)
        exact.setdefault(q.effective_notation, mr_name)
        base = q.label_template.split(" / ", 1)[0].strip()
        by_base.setdefault(base.lower(), mr_name)
        stem = _slugify(base).replace("-", "_")
        notation = q.effective_notation
        if q.unit not in _DIMENSIONLESS and notation.startswith(stem + "_"):
            stems.append((stem, notation[len(stem) + 1 :], mr_name))
    stems.sort(key=lambda s: len(s[0]), reverse=True)
    index = _NameIndex(exact, by_base, tuple(stems))
    _index_cache.clear()
    _index_cache[key] = index
    return index


def _compatible_unit(q: Quantity, unit: str) -> str | None:
    """``unit`` if it converts to the quantity's canonical unit, else None."""
    if unit in ("C", "c") and q.unit in _TEMPERATURE_UNITS:
        unit = "degC"
    if q.unit in _DIMENSIONLESS:
        return None
    return unit if get_unit_conversion(unit, q.unit) is not None else None


def _machine_unit(q: Quantity, token: str, suffix: str) -> str | None:
    """Unit named by the token after a machine-readable stem, or None."""
    if token == suffix:
        return q.unit
    unit = _compatible_unit(q, token)
    if unit is not None:
        return unit
    for prefix in _LOWERCASE_PREFIXES:
        if token == prefix + suffix:
            return _compatible_unit(q, f"{prefix}{q.unit}")
    return None


def _parse(name: str) -> ColumnName | str:
    """Parse a column name; on failure return the reason as a string."""
    index = _name_index()
    mr_name = index.exact.get(name)
    if mr_name is not None:
        return ColumnName(mr_name, COLUMN_ONTOLOGY[mr_name].unit)

    parsed = parse_label(name)
    if parsed is not None:
        base, unit = parsed
        mr_name = index.by_base.get(base.lower())
        if mr_name is not None:
            q = COLUMN_ONTOLOGY[mr_name]
            compatible = _compatible_unit(q, unit)
            if compatible is not None:
                return ColumnName(mr_name, compatible)
            return f"unit {unit!r} does not convert to {q.formatted_label!r}"

    reason = f"{name!r} is not a BDF preferred label or machine-readable name"
    for stem, suffix, mr_name in index.stems:
        if not name.startswith(stem + "_"):
            continue
        token = name[len(stem) + 1 :]
        q = COLUMN_ONTOLOGY[mr_name]
        machine_unit = _machine_unit(q, token, suffix)
        if machine_unit is not None:
            return ColumnName(mr_name, machine_unit)
        reason = (
            f"{token!r} is not a unit of {mr_name!r} ({q.unit}); unit symbols are case-sensitive, "
            f"e.g. '{stem}_m{q.unit}' or '{stem}_milli{suffix}'"
        )
        break
    return reason


def parse_name(name: str) -> ColumnName:
    """Parse a preferred label or machine-readable name, with optional unit.

    Args:
        name: E.g. ``"Voltage / mV"``, ``"voltage_volt"``, ``"test_time_h"``.

    Returns:
        The quantity and unit the name asks for.

    Raises:
        BDFColumnError: If the name does not identify a BDF quantity, or names
            a unit that does not convert to the quantity's canonical unit.

    Examples:
        >>> parse_name("test_time_h")
        ColumnName(quantity='test_time_second', unit='h')
        >>> parse_name("Step ID")
        ColumnName(quantity='step_id', unit=None)
    """
    parsed = _parse(name)
    if isinstance(parsed, str):
        raise BDFColumnError(f"Cannot parse column {name!r}: {parsed}.")
    return parsed


# --------- Resolution ----------


def _convert(expr: pl.Expr, src_unit: str | None, dst_unit: str | None) -> pl.Expr:
    """Apply the linear conversion between two compatible units."""
    if src_unit == dst_unit or src_unit in _DIMENSIONLESS or dst_unit in _DIMENSIONLESS:
        return expr
    if dst_unit in ("C", "c"):
        dst_unit = "degC"
    conversion = get_unit_conversion(src_unit, dst_unit)
    if conversion is None:
        raise BDFColumnError(f"Cannot convert {src_unit!r} to {dst_unit!r}.")
    scale, offset = conversion
    if scale == 1.0 and offset == 0.0:
        return expr
    out = expr.cast(pl.Float64)
    if scale != 1.0:
        out = out * scale
    if offset != 0.0:
        out = out + offset
    return out


class _Unresolvable(Exception):
    """Internal: a quantity cannot be resolved; ``args[0]`` explains why."""


class _Resolver:
    """Resolves quantities against one set of available column names."""

    def __init__(self, available: Iterable[str]) -> None:
        self.available = list(dict.fromkeys(available))
        self.recorded: dict[str, tuple[str, str | None]] = {}
        for column in self.available:
            parsed = _parse(column)
            if isinstance(parsed, str):
                continue
            canonical_unit = COLUMN_ONTOLOGY[parsed.quantity].unit
            current = self.recorded.get(parsed.quantity)
            # Prefer a recorded column already in the canonical unit.
            if current is None or (current[1] != canonical_unit and parsed.unit == canonical_unit):
                self.recorded[parsed.quantity] = (column, parsed.unit)
        self._resolved: dict[str, pl.Expr] = {}

    def canonical(self, quantity: str, stack: frozenset[str] = frozenset()) -> pl.Expr:
        """Expression for ``quantity`` in its canonical unit.

        Raises:
            _Unresolvable: If neither the data nor any recipe provides it.
        """
        if quantity in self._resolved:
            return self._resolved[quantity]
        if quantity in stack:
            raise _Unresolvable(f"{quantity!r} (circular)")
        q = COLUMN_ONTOLOGY.get(quantity)
        if q is None:
            raise _Unresolvable(f"{quantity!r} is not in the loaded ontology")
        if quantity in self.recorded:
            column, unit = self.recorded[quantity]
            expr = _convert(pl.col(column), unit, q.unit)
        else:
            expr = self._from_recipes(quantity, stack | {quantity})
        self._resolved[quantity] = expr
        return expr

    def _from_recipes(self, quantity: str, stack: frozenset[str]) -> pl.Expr:
        recipes = RECIPES.get(quantity, [])
        if not recipes:
            raise _Unresolvable(f"{quantity!r} is not in the data, and no recipe derives it")
        direct = [r for r in recipes if all(i in self.recorded for i in r.inputs)]
        chained = [r for r in recipes if r not in direct]
        failures: list[str] = []
        for recipe in direct + chained:
            try:
                return self._apply(recipe, stack)
            except _Unresolvable as exc:
                failures.append(f"\n  - {recipe.description or recipe.inputs}: needs {exc.args[0]}")
        raise _Unresolvable(f"{quantity!r} is not in the data, and no recipe applies:{''.join(failures)}")

    def _apply(self, recipe: Recipe, stack: frozenset[str]) -> pl.Expr:
        missing = [i for i in recipe.inputs if i not in self.recorded and not RECIPES.get(i)]
        if missing:
            raise _Unresolvable(", ".join(missing))
        inputs = []
        for name in recipe.inputs:
            try:
                inputs.append(self.canonical(name, stack))
            except _Unresolvable:
                raise _Unresolvable(name) from None
        return recipe.compute(*inputs)

    def request(self, name: str) -> pl.Expr:
        """Expression for a requested column name, aliased to that name."""
        if name in self.available:
            return pl.col(name)
        target = parse_name(name)
        try:
            expr = self.canonical(target.quantity)
        except _Unresolvable as exc:
            msg = f"Cannot resolve {name!r} from columns {self.available}: {exc.args[0]}"
            raise BDFColumnError(msg) from None
        q = COLUMN_ONTOLOGY[target.quantity]
        return _convert(expr, q.unit, target.unit).alias(name)


def _flatten(columns: Sequence[str | Iterable[str]]) -> list[str]:
    names: list[str] = []
    for item in columns:
        if isinstance(item, str):
            names.append(item)
        else:
            names.extend(item)
    return list(dict.fromkeys(names))


def resolve(columns: str | Iterable[str], available: Iterable[str]) -> dict[str, pl.Expr]:
    """Build an expression for each requested column from the available ones.

    Needs only column names, never data, so it can plan against any schema.

    Args:
        columns: Requested column name(s), as preferred labels or
            machine-readable names, optionally with a unit.
        available: Column names present in the data.

    Returns:
        Mapping from each requested name to a Polars expression aliased to it.

    Raises:
        BDFColumnError: If a request cannot be parsed, or neither the data nor
            any recipe provides it.

    Examples:
        >>> exprs = resolve(["power_watt", "Voltage / mV"], ["Current / A", "Voltage / V"])
        >>> list(exprs)
        ['power_watt', 'Voltage / mV']
    """
    resolver = _Resolver(available)
    return {name: resolver.request(name) for name in _flatten([columns])}


def can_resolve(column: str, available: Iterable[str]) -> bool:
    """Check whether :func:`resolve` would succeed for one column.

    Args:
        column: Requested column name.
        available: Column names present in the data.

    Returns:
        True if the column can be selected or derived from ``available``.
    """
    try:
        resolve(column, available)
    except BDFColumnError:
        return False
    return True


def _apply(df: Any, columns: Sequence[str | Iterable[str]], *, keep: bool) -> Any:
    kind = _classify_df(df)
    lf = _to_polars_lazy(df)
    exprs = resolve(_flatten(columns), lf.collect_schema().names())
    out = lf.with_columns(list(exprs.values())) if keep else lf.select(list(exprs.values()))
    return _from_polars_lazy(out, kind)


def with_columns(df: AnyDF, *columns: str | Iterable[str]) -> AnyDF:
    """Return ``df`` with the requested BDF columns added.

    Columns already present are kept as they are; the rest are converted from
    a recorded column of the same quantity, or derived by recipe.

    Args:
        df: pandas DataFrame, polars DataFrame, or polars LazyFrame, rows in
            time order for one test.
        *columns: Requested column names, or iterables of them.

    Returns:
        A frame of the same kind as ``df``.

    Raises:
        BDFColumnError: If a requested column cannot be resolved.

    Examples:
        >>> import polars as pl
        >>> df = pl.DataFrame({"Current / A": [1.0, 2.0], "Voltage / V": [3.0, 4.0]})
        >>> with_columns(df, "Power / W", "current_mA").columns
        ['Current / A', 'Voltage / V', 'Power / W', 'current_mA']
    """
    return _apply(df, columns, keep=True)


def select(df: AnyDF, *columns: str | Iterable[str]) -> AnyDF:
    """Return only the requested BDF columns of ``df``, in the requested order.

    Args:
        df: pandas DataFrame, polars DataFrame, or polars LazyFrame, rows in
            time order for one test.
        *columns: Requested column names, or iterables of them.

    Returns:
        A frame of the same kind as ``df``.

    Raises:
        BDFColumnError: If a requested column cannot be resolved.

    Examples:
        >>> import polars as pl
        >>> df = pl.DataFrame({"Test Time / s": [0.0, 3600.0], "Voltage / V": [3.0, 4.0]})
        >>> select(df, "test_time_ms", "Voltage / mV").rows()
        [(0.0, 3000.0), (3600000.0, 4000.0)]
    """
    return _apply(df, columns, keep=False)
