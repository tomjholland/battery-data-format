"""Ask for any BDF column by name, whether or not the data records it.

A request names a BDF quantity by its preferred label or its machine-readable
name:

- preferred label: ``"Voltage / V"``, or in another unit, ``"Voltage / mV"``
- machine-readable name: ``"voltage_volt"``, always in the canonical unit

Resolution tries, in order: a column of that exact name; a recorded column of
the same quantity; then the recipes in :data:`bdf.recipes.RECIPES`, preferring
a recipe whose inputs are all recorded over one that needs inputs derived
first.

Units convert only through preferred labels, and only when the label names a
unit other than the canonical one: ``"Voltage / mV"`` converts, ``"Voltage /
V"`` and ``"voltage_volt"`` do not. Machine-readable names are the ontology's
fixed identifiers, so ``"voltage_mV"`` is not a BDF name and raises. A
recorded column in a non-canonical unit (``"Voltage / mV"`` in the data) is
converted to the canonical unit before any recipe uses it. All conversions go
through :meth:`bdf.spec.Quantity.convert_to` and
:meth:`~bdf.spec.Quantity.convert_from`.

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
from bdf.spec import COLUMN_ONTOLOGY, Quantity, parse_label

if TYPE_CHECKING:
    from bdf._df_compat import AnyDF

__all__ = ["ColumnName", "can_resolve", "parse_name", "resolve", "select", "with_columns"]

_DIMENSIONLESS = (None, "1")
_TEMPERATURE_UNITS = frozenset({"degC", "K", "degF"})


@dataclass(frozen=True)
class ColumnName:
    """A column name parsed into its BDF quantity and the unit it names.

    Attributes:
        quantity: Machine-readable name of the quantity, e.g. ``"voltage_volt"``.
        unit: The unit the name asks for, e.g. ``"mV"``; the canonical unit
            for a machine-readable name, and ``None`` for unitless quantities.

    Examples:
        >>> parse_name("Voltage / mV")
        ColumnName(quantity='voltage_volt', unit='mV')
        >>> parse_name("voltage_volt")
        ColumnName(quantity='voltage_volt', unit='V')
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


_index_cache: dict[int, _NameIndex] = {}


def _name_index() -> _NameIndex:
    """Lookup tables for the loaded ontology, rebuilt if it is reloaded."""
    key = id(COLUMN_ONTOLOGY._quantities)
    cached = _index_cache.get(key)
    if cached is not None:
        return cached
    exact: dict[str, str] = {}
    by_base: dict[str, str] = {}
    for mr_name, q in COLUMN_ONTOLOGY:
        if q.deprecated:
            continue
        exact.setdefault(q.formatted_label, mr_name)
        exact.setdefault(q.effective_notation, mr_name)
        by_base.setdefault(q.label_template.split(" / ", 1)[0].strip().lower(), mr_name)
    index = _NameIndex(exact, by_base)
    _index_cache.clear()
    _index_cache[key] = index
    return index


def _compatible_unit(q: Quantity, unit: str) -> str | None:
    """``unit`` if it converts to the quantity's canonical unit, else None."""
    if q.unit in _DIMENSIONLESS:
        return None
    if unit in ("C", "c") and q.unit in _TEMPERATURE_UNITS:
        unit = "degC"
    return unit if q.convert_from(unit) is not None else None


def _machine_name_hint(name: str) -> str:
    """Point a unit-bearing machine-readable name at the preferred label."""
    best: Quantity | None = None
    best_stem = ""
    for _, q in COLUMN_ONTOLOGY:
        if q.deprecated or q.unit in _DIMENSIONLESS:
            continue
        stem = q.effective_notation.rsplit("_", 1)[0]
        if name.startswith(stem + "_") and len(stem) > len(best_stem):
            best, best_stem = q, stem
    if best is None:
        return ""
    base = best.label_template.split(" / ", 1)[0]
    return (
        f"; machine-readable names are fixed, so ask for {best.effective_notation!r}, "
        f"or a preferred label with a unit such as '{base} / <unit>'"
    )


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

    return f"{name!r} is not a BDF preferred label or machine-readable name{_machine_name_hint(name)}"


def parse_name(name: str) -> ColumnName:
    """Parse a preferred label, with optional unit, or a machine-readable name.

    Args:
        name: E.g. ``"Voltage / V"``, ``"Voltage / mV"``, ``"voltage_volt"``.

    Returns:
        The quantity and unit the name asks for.

    Raises:
        BDFColumnError: If the name does not identify a BDF quantity, or names
            a unit that does not convert to the quantity's canonical unit.

    Examples:
        >>> parse_name("Test Time / h")
        ColumnName(quantity='test_time_second', unit='h')
        >>> parse_name("Step ID")
        ColumnName(quantity='step_id', unit=None)
    """
    parsed = _parse(name)
    if isinstance(parsed, str):
        raise BDFColumnError(f"Cannot parse column {name!r}: {parsed}.")
    return parsed


# --------- Resolution ----------


def _scale(expr: pl.Expr, conversion: tuple[float, float] | None, src: str | None, dst: str | None) -> pl.Expr:
    """Apply a ``(scale, offset)`` conversion from :class:`~bdf.spec.Quantity`."""
    if conversion is None:
        raise BDFColumnError(f"Cannot convert {src!r} to {dst!r}.")
    scale, offset = conversion
    if scale == 1.0 and offset == 0.0:
        return expr
    out = expr.cast(pl.Float64)
    if scale != 1.0:
        out = out * scale
    if offset != 0.0:
        out = out + offset
    return out


def _to_canonical(expr: pl.Expr, q: Quantity, unit: str | None) -> pl.Expr:
    """Convert ``expr`` from ``unit`` to the quantity's canonical unit."""
    if unit == q.unit or q.unit in _DIMENSIONLESS:
        return expr
    return _scale(expr, q.convert_from(unit), unit, q.unit)


def _from_canonical(expr: pl.Expr, q: Quantity, unit: str | None) -> pl.Expr:
    """Convert ``expr`` from the quantity's canonical unit to ``unit``."""
    if unit == q.unit or q.unit in _DIMENSIONLESS:
        return expr
    return _scale(expr, q.convert_to(unit), q.unit, unit)


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
            expr = _to_canonical(pl.col(column), q, unit)
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
        return _from_canonical(expr, COLUMN_ONTOLOGY[target.quantity], target.unit).alias(name)


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
        >>> with_columns(df, "Power / W", "Current / mA").columns
        ['Current / A', 'Voltage / V', 'Power / W', 'Current / mA']
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
        >>> select(df, "Test Time / ms", "voltage_volt").rows()
        [(0.0, 3.0), (3600000.0, 4.0)]
    """
    return _apply(df, columns, keep=False)
