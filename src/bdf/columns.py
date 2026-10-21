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
from typing import TYPE_CHECKING, Any

import polars as pl

from bdf import _polars_namespace  # noqa: F401  (registers the `bdf` namespace)
from bdf._df_compat import _classify_df, _from_polars_lazy, _to_polars_lazy
from bdf._errors import BDFColumnError
from bdf.normalization import LinearNormalization
from bdf.recipes import RECIPES, Recipe
from bdf.spec import _TEMPERATURE_DST_UNITS, COLUMN_ONTOLOGY, Quantity

if TYPE_CHECKING:
    from bdf._df_compat import AnyDF

__all__ = ["can_resolve", "parse_name", "resolve", "select", "with_columns"]

_DIMENSIONLESS = (None, "1")


# --------- Name parsing ----------


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


def _parse(name: str) -> tuple[Quantity, str | None] | str:
    """Parse a column name; on failure return the reason as a string."""
    for _, q in COLUMN_ONTOLOGY:
        if not q.deprecated and q.effective_notation == name:
            return q, q.unit

    match = COLUMN_ONTOLOGY.quantity_from_label(name)
    if match is None or match[0].deprecated:
        return f"{name!r} is not a BDF preferred label or machine-readable name{_machine_name_hint(name)}"
    q, unit = match
    if unit is None or unit == q.unit:
        return q, q.unit
    if unit in ("C", "c") and q.unit in _TEMPERATURE_DST_UNITS:
        # get_unit_conversion reads a bare "C" as Celsius only as the source unit.
        unit = "degC"
    if q.unit in _DIMENSIONLESS or q.convert_from(unit) is None:
        return f"unit {unit!r} does not convert to {q.formatted_label!r}"
    return q, unit


def parse_name(name: str) -> tuple[Quantity, str | None]:
    """Parse a preferred label, with optional unit, or a machine-readable name.

    Args:
        name: E.g. ``"Voltage / V"``, ``"Voltage / mV"``, ``"voltage_volt"``.

    Returns:
        The quantity and the unit the name asks for: the canonical unit for a
        machine-readable name, ``None`` for unitless quantities. The same
        shape as :meth:`bdf.spec.ColumnOntology.quantity_from_label`.

    Raises:
        BDFColumnError: If the name does not identify a BDF quantity, or names
            a unit that does not convert to the quantity's canonical unit.

    Examples:
        >>> q, unit = parse_name("Test Time / h")
        >>> q.mr_name, unit
        ('test_time_second', 'h')
        >>> q, unit = parse_name("voltage_volt")
        >>> q.mr_name, unit
        ('voltage_volt', 'V')
    """
    parsed = _parse(name)
    if isinstance(parsed, str):
        raise BDFColumnError(f"Cannot parse column {name!r}: {parsed}.")
    return parsed


# --------- Resolution ----------


def _convert(expr: pl.Expr, conversion: tuple[float, float] | None, src: str | None, dst: str | None) -> pl.Expr:
    """Apply a ``(scale, offset)`` from :class:`~bdf.spec.Quantity` as a :class:`LinearNormalization`."""
    if src == dst:
        return expr
    if conversion is None:
        raise BDFColumnError(f"Cannot convert {src!r} to {dst!r}.")
    scale, offset = conversion
    return LinearNormalization(scale=scale, offset=offset).expr(expr)


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
            q, unit = parsed
            current = self.recorded.get(q.mr_name)
            # Prefer a recorded column already in the canonical unit.
            if current is None or (current[1] != q.unit and unit == q.unit):
                self.recorded[q.mr_name] = (column, unit)
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
            expr = _convert(pl.col(column), q.convert_from(unit), unit, q.unit)
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
        q, unit = parse_name(name)
        try:
            expr = self.canonical(q.mr_name)
        except _Unresolvable as exc:
            msg = f"Cannot resolve {name!r} from columns {self.available}: {exc.args[0]}"
            raise BDFColumnError(msg) from None
        return _convert(expr, q.convert_to(unit), q.unit, unit).alias(name)


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
        columns: Requested column name(s), as preferred labels (optionally in
            another unit) or machine-readable names.
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
