"""Ask for any BDF column by name, recorded or computed from other BDF quantities.

A request names a BDF quantity by its preferred label or its machine-readable
name:

- preferred label: ``"Voltage / V"``, or in another unit, ``"Voltage / mV"``
- machine-readable name: ``"voltage_volt"``, always in the canonical unit

Names are matched by :meth:`bdf.spec.ColumnOntology.quantity_from_name`, and
units convert through :mod:`bdf.units`, only for a preferred label that names
a non-canonical unit. A recorded column in a non-canonical unit is converted
to the canonical unit before any recipe uses it.

Resolution tries, in order: a column of that exact name; a recorded column of
the same quantity; then the recipes in :data:`RECIPES`. A :class:`Recipe`
names the quantities it needs (by machine-readable name) and a function that
turns their Polars expressions into the target. Each quantity's recipes run
most trustworthy first, preferring a recipe whose inputs are all recorded over
one that needs inputs derived first.

Every expression a recipe receives is in its quantity's canonical BDF unit
(``current_ampere`` in A, ``test_time_second`` in s, ...), and every recipe
returns its target in the target's canonical unit, so recipes never deal with
units. The ontology (``prov:wasDerivedFrom``, ``latexFormula``) says *what* a
derived quantity is; the recipes say *how* to compute it from sampled data,
with fallbacks for when the ideal inputs are missing.

Recipes that accumulate (``cum_sum``, ``diff``) assume rows are in time order
for one test. Derive before filtering rows, not after. Scoped recipes assume
step and cycle counts never decrease, as the ontology defines them.

:func:`resolve` works from column names alone and returns Polars expressions,
so it serves any frame type. :func:`select` and :func:`with_columns` apply
those expressions to a pandas DataFrame, polars DataFrame, or polars
LazyFrame, and return the same kind. Importing this module also registers the
``bdf`` namespace on polars frames: ``df.bdf.with_columns("power_watt")``.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import polars as pl

from bdf import _polars_namespace  # noqa: F401  (registers the `bdf` namespace)
from bdf._df_compat import _classify_df, _from_polars_lazy, _to_polars_lazy
from bdf._errors import BDFColumnError
from bdf.normalization import LinearNormalization
from bdf.spec import COLUMN_ONTOLOGY, Quantity

if TYPE_CHECKING:
    from bdf._df_compat import AnyDF

__all__ = ["RECIPES", "Recipe", "can_resolve", "register_recipe", "resolve", "select", "with_columns"]


@dataclass(frozen=True)
class Recipe:
    """A rule that computes one BDF quantity from other BDF quantities.

    Attributes:
        inputs: Machine-readable names of the quantities the recipe needs, in
            the order ``compute`` takes them.
        compute: Function taking one Polars expression per input, each in its
            canonical BDF unit, and returning the target in its canonical unit.
        description: Short human-readable summary, shown in error messages.

    Examples:
        >>> power = Recipe(("current_ampere", "voltage_volt"), lambda i, v: i * v, "V * I")
        >>> power.inputs
        ('current_ampere', 'voltage_volt')
    """

    inputs: tuple[str, ...]
    compute: Callable[..., pl.Expr]
    description: str = ""

    def __post_init__(self) -> None:
        """Check that ``compute`` takes exactly one argument per input.

        Raises:
            TypeError: If ``compute`` cannot be called with ``len(inputs)``
                positional arguments.
        """
        object.__setattr__(self, "inputs", tuple(self.inputs))
        try:
            inspect.signature(self.compute).bind(*self.inputs)
        except TypeError as exc:
            msg = f"Recipe compute must take {len(self.inputs)} positional argument(s), one per input {self.inputs}"
            raise TypeError(msg) from exc
        except ValueError:
            # Builtins without an introspectable signature; trust the caller.
            pass


# --------- Building blocks ----------


def _trapz(rate: pl.Expr, time: pl.Expr) -> pl.Expr:
    """Running trapezoidal integral of ``rate`` over ``time``, divided by 3600.

    Integrates current (A) to charge (Ah), or power (W) to energy (Wh).

    Args:
        rate: Current or power expression.
        time: Elapsed time in seconds.

    Returns:
        Expression starting at zero on the first row.
    """
    rate = rate.cast(pl.Float64)
    dt = time.cast(pl.Float64).diff().fill_null(0.0)
    return (0.5 * (rate + rate.shift(1)) * dt / 3600.0).fill_null(0.0).cum_sum()


def _seam(rate: pl.Expr, time: pl.Expr, key: pl.Expr) -> pl.Expr:
    """Trapezoidal charge across each ``key`` boundary, zero elsewhere.

    A column that resets at each step loses the interval from the last row of
    one step to the first row of the next. This is that lost amount, so it can
    be added back.

    Args:
        rate: Current or power expression.
        time: Elapsed time in seconds.
        key: Group key whose changes mark the resets (e.g. step count).

    Returns:
        Expression holding the seam term on boundary rows and zero elsewhere.
    """
    rate = rate.cast(pl.Float64)
    dt = time.cast(pl.Float64).diff().fill_null(0.0)
    seam = 0.5 * (rate + rate.shift(1)) * dt / 3600.0
    is_boundary = key.cast(pl.Int64).diff().fill_null(0) != 0
    return pl.when(is_boundary).then(seam).otherwise(0.0).fill_null(0.0)


def _increments(resetting: pl.Expr) -> pl.Expr:
    """Non-negative row-to-row increments of a resetting counter.

    Args:
        resetting: A charging or discharging column that drops back at resets.

    Returns:
        Expression of increments, with the drops at resets clipped to zero.
    """
    return resetting.cast(pl.Float64).diff().clip(lower_bound=0).fill_null(0.0)


def _group_start(key: pl.Expr) -> pl.Expr:
    """True on the first row and wherever ``key`` changes."""
    return key.cast(pl.Int64).diff().fill_null(1) != 0


def _first_in_group(value: pl.Expr, key: pl.Expr) -> pl.Expr:
    """Value of ``value`` on the first row of each run of constant ``key``.

    Deliberately not ``value.first().over(key)``: a window evaluates the whole
    of ``value`` within each group, so a derived ``value`` built on
    ``cum_sum`` or ``diff`` would restart inside the group and give the wrong
    baseline. Step and cycle counts never decrease, so each key value is one
    contiguous run and this frame-wide form is equivalent for recorded
    columns.

    Args:
        value: Expression to take the group baseline of.
        key: Group key that is constant within each run (e.g. step count).

    Returns:
        Expression broadcasting each run's first value over the run.
    """
    return pl.when(_group_start(key)).then(value).forward_fill()


def _reset_key(charge: pl.Expr, discharge: pl.Expr) -> pl.Expr:
    """Synthetic segment key that increments wherever either counter drops.

    Args:
        charge: A resetting charging column.
        discharge: A resetting discharging column.

    Returns:
        Integer expression, constant between resets.
    """
    dropped = (charge.cast(pl.Float64).diff() < 0) | (discharge.cast(pl.Float64).diff() < 0)
    return dropped.fill_null(False).cum_sum()


# --------- Recipe factories ----------


def _within_net(charge: str, discharge: str) -> Recipe:
    """``net = charge - discharge``, valid when both reset together."""
    return Recipe((charge, discharge), lambda c, d: c - d, f"{charge} - {discharge}")


def _within_cumulative(charge: str, discharge: str) -> Recipe:
    """``cumulative = charge + discharge``, valid when both reset together."""
    return Recipe((charge, discharge), lambda c, d: c + d, f"{charge} + {discharge}")


def _net_from_step_counters(charge: str, discharge: str, rate: str) -> Recipe:
    """Never-resetting net from step-resetting charge/discharge, seams added back."""

    def compute(c: pl.Expr, d: pl.Expr, r: pl.Expr, t: pl.Expr, k: pl.Expr) -> pl.Expr:
        return (_increments(c) - _increments(d) + _seam(r, t, k)).cum_sum()

    return Recipe(
        (charge, discharge, rate, "test_time_second", "step_count"),
        compute,
        f"accumulate {charge} - {discharge} across step resets",
    )


def _cumulative_from_step_counters(charge: str, discharge: str, rate: str) -> Recipe:
    """Never-resetting throughput from step-resetting charge/discharge, seams added back."""

    def compute(c: pl.Expr, d: pl.Expr, r: pl.Expr, t: pl.Expr, k: pl.Expr) -> pl.Expr:
        return (_increments(c) + _increments(d) + _seam(r, t, k).abs()).cum_sum()

    return Recipe(
        (charge, discharge, rate, "test_time_second", "step_count"),
        compute,
        f"accumulate {charge} + {discharge} across step resets",
    )


def _net_from_resetting_counters(charge: str, discharge: str, rate: str) -> Recipe:
    """Never-resetting net from counters that reset at unknown points (schedule-defined)."""

    def compute(c: pl.Expr, d: pl.Expr, r: pl.Expr, t: pl.Expr) -> pl.Expr:
        return (_increments(c) - _increments(d) + _seam(r, t, _reset_key(c, d))).cum_sum()

    return Recipe(
        (charge, discharge, rate, "test_time_second"),
        compute,
        f"accumulate {charge} - {discharge} across detected resets",
    )


def _cumulative_from_resetting_counters(charge: str, discharge: str, rate: str) -> Recipe:
    """Never-resetting throughput from counters that reset at unknown points."""

    def compute(c: pl.Expr, d: pl.Expr, r: pl.Expr, t: pl.Expr) -> pl.Expr:
        return (_increments(c) + _increments(d) + _seam(r, t, _reset_key(c, d)).abs()).cum_sum()

    return Recipe(
        (charge, discharge, rate, "test_time_second"),
        compute,
        f"accumulate {charge} + {discharge} across detected resets",
    )


def _global_from_step_scoped(step_value: str, rate: str, *, absolute: bool) -> Recipe:
    """Never-resetting net (or throughput) from its step-scoped counterpart.

    Within a step the increments carry over; at each step boundary, where the
    scoped column resets, the seam term stands in for the lost interval.
    """

    def compute(v: pl.Expr, r: pl.Expr, t: pl.Expr, k: pl.Expr) -> pl.Expr:
        seam = _seam(r, t, k)
        increments = (
            pl.when(_group_start(k)).then(seam.abs() if absolute else seam).otherwise(v.cast(pl.Float64).diff())
        )
        return increments.fill_null(0.0).cum_sum()

    return Recipe(
        (step_value, rate, "test_time_second", "step_count"),
        compute,
        f"accumulate {step_value} across step resets",
    )


def _net_from_cumulative(cumulative: str) -> Recipe:
    """Signed net from unsigned throughput, taking the sign from the current."""

    def compute(q: pl.Expr, i: pl.Expr) -> pl.Expr:
        return (i.cast(pl.Float64).sign() * q.cast(pl.Float64).diff().fill_null(0.0)).cum_sum()

    return Recipe((cumulative, "current_ampere"), compute, f"sign(current) * d({cumulative})")


def _cumulative_from_net(net: str) -> Recipe:
    """Unsigned throughput as the running sum of ``|d(net)|``."""

    def compute(n: pl.Expr) -> pl.Expr:
        return n.cast(pl.Float64).diff().abs().fill_null(0.0).cum_sum()

    return Recipe((net,), compute, f"running sum of |d({net})|")


def _component_from_net(net: str, sign: float, key: str | None = None) -> Recipe:
    """Charging (``sign=1``) or discharging (``sign=-1``) part of a net column.

    Assumes charge and discharge never both flow within one sampling interval.
    A scoped net column passes its scope ``key`` so the sum restarts with it.
    """
    part = "positive" if sign > 0 else "negative"

    def increments(n: pl.Expr) -> pl.Expr:
        return (sign * n.cast(pl.Float64).diff()).clip(lower_bound=0).fill_null(0.0)

    if key is None:
        return Recipe((net,), lambda n: increments(n).cum_sum(), f"running sum of {part} d({net})")

    def scoped(n: pl.Expr, k: pl.Expr) -> pl.Expr:
        # The scoped net restarts at each boundary, so the jump there is not an increment.
        total = pl.when(_group_start(k)).then(0.0).otherwise(increments(n)).cum_sum()
        return total - _first_in_group(total, k)

    return Recipe((net, key), scoped, f"running sum of {part} d({net}) within each {key}")


def _scope_reset(value: str, key: str) -> Recipe:
    """Restart a never-resetting quantity at zero within each ``key`` group."""

    def compute(v: pl.Expr, k: pl.Expr) -> pl.Expr:
        return v - _first_in_group(v, k)

    return Recipe((value, key), compute, f"{value} minus its first value within each {key}")


def _integral(rate: str, transform: Callable[[pl.Expr], pl.Expr], what: str) -> Recipe:
    """Trapezoidal integral of ``transform(rate)`` over test time.

    The least trustworthy reconstruction: it depends on the sampling rate and
    misses anything between samples. Recipes that use a recorded charge or
    energy column come first wherever one exists.
    """

    def compute(r: pl.Expr, t: pl.Expr) -> pl.Expr:
        return _trapz(transform(r.cast(pl.Float64)), t)

    return Recipe((rate, "test_time_second"), compute, f"trapezoidal integral of {what} over test time")


def _positive(e: pl.Expr) -> pl.Expr:
    return e.clip(lower_bound=0)


def _negative(e: pl.Expr) -> pl.Expr:
    return (-e).clip(lower_bound=0)


def _identity(e: pl.Expr) -> pl.Expr:
    return e


def _test_time_from_unix_time(unix: pl.Expr) -> pl.Expr:
    unix = unix.cast(pl.Float64)
    return unix - unix.first()


def _step_time_from_test_time(time: pl.Expr, step: pl.Expr) -> pl.Expr:
    time = time.cast(pl.Float64)
    return time - _first_in_group(time, step)


def _step_count_from_step_id(step_id: pl.Expr) -> pl.Expr:
    return step_id.cast(pl.Int64).diff().fill_null(0).ne(0).cum_sum().cast(pl.UInt64)


def _power(current: pl.Expr, voltage: pl.Expr) -> pl.Expr:
    return current.cast(pl.Float64) * voltage.cast(pl.Float64)


def _impedance_modulus(real: pl.Expr, imaginary: pl.Expr) -> pl.Expr:
    return (real.cast(pl.Float64) ** 2 + imaginary.cast(pl.Float64) ** 2).sqrt()


def _phase(real: pl.Expr, imaginary: pl.Expr) -> pl.Expr:
    return pl.arctan2(imaginary.cast(pl.Float64), real.cast(pl.Float64)).degrees()


def _quantity_recipes(kind: str, unit: str, rate: str) -> dict[str, list[Recipe]]:
    """Recipes for one family: capacity (``Ah``, from current) or energy (``Wh``, from power).

    Args:
        kind: ``"capacity"`` or ``"energy"``.
        unit: Machine-readable unit suffix, ``"ah"`` or ``"wh"``.
        rate: The rate quantity integrated to this family.

    Returns:
        Mapping from target quantity to its recipes, most trustworthy first.
    """

    def name(scope: str, component: str) -> str:
        return f"{scope}{component}_{kind}_{unit}"

    out: dict[str, list[Recipe]] = {
        name("", "net"): [
            _within_net(name("", "charging"), name("", "discharging")),
            _net_from_step_counters(name("step_", "charging"), name("step_", "discharging"), rate),
            _global_from_step_scoped(name("step_", "net"), rate, absolute=False),
            _net_from_resetting_counters(name("schedule_", "charging"), name("schedule_", "discharging"), rate),
            _net_from_cumulative(name("", "cumulative")),
            _integral(rate, _identity, rate),
        ],
        name("", "cumulative"): [
            _within_cumulative(name("", "charging"), name("", "discharging")),
            _cumulative_from_net(name("", "net")),
            _cumulative_from_step_counters(name("step_", "charging"), name("step_", "discharging"), rate),
            _global_from_step_scoped(name("step_", "cumulative"), rate, absolute=True),
            _cumulative_from_resetting_counters(name("schedule_", "charging"), name("schedule_", "discharging"), rate),
            _integral(rate, pl.Expr.abs, f"|{rate}|"),
        ],
        name("", "charging"): [
            _component_from_net(name("", "net"), 1.0),
            _integral(rate, _positive, f"max({rate}, 0)"),
        ],
        name("", "discharging"): [
            _component_from_net(name("", "net"), -1.0),
            _integral(rate, _negative, f"max(-{rate}, 0)"),
        ],
    }
    for scope, key in (("step_", "step_count"), ("cycle_", "cycle_count")):
        out[name(scope, "net")] = [
            _within_net(name(scope, "charging"), name(scope, "discharging")),
            _scope_reset(name("", "net"), key),
        ]
        out[name(scope, "cumulative")] = [
            _within_cumulative(name(scope, "charging"), name(scope, "discharging")),
            _scope_reset(name("", "cumulative"), key),
        ]
        out[name(scope, "charging")] = [
            _scope_reset(name("", "charging"), key),
            _component_from_net(name(scope, "net"), 1.0, key),
        ]
        out[name(scope, "discharging")] = [
            _scope_reset(name("", "discharging"), key),
            _component_from_net(name(scope, "net"), -1.0, key),
        ]
    return out


RECIPES: dict[str, list[Recipe]] = {
    "test_time_second": [
        Recipe(("unix_time_second",), _test_time_from_unix_time, "unix_time_second - its first value")
    ],
    "step_time_second": [
        Recipe(
            ("test_time_second", "step_count"),
            _step_time_from_test_time,
            "test_time_second minus its first value within each step_count",
        )
    ],
    "step_count": [Recipe(("step_id",), _step_count_from_step_id, "count of step_id changes")],
    "power_watt": [Recipe(("current_ampere", "voltage_volt"), _power, "current_ampere * voltage_volt")],
    "absolute_impedance_ohm": [
        Recipe(("real_impedance_ohm", "imaginary_impedance_ohm"), _impedance_modulus, "sqrt(Z_re^2 + Z_im^2)")
    ],
    "phase_degree": [Recipe(("real_impedance_ohm", "imaginary_impedance_ohm"), _phase, "atan2(Z_im, Z_re) in degrees")],
    **_quantity_recipes("capacity", "ah", "current_ampere"),
    **_quantity_recipes("energy", "wh", "power_watt"),
}
"""Recipes for each derivable quantity, keyed by machine-readable name, most trustworthy first.

Resolution prefers a recorded column, then the first recipe whose inputs are
all recorded, then the first recipe whose inputs can themselves be derived.
Extend it with :func:`register_recipe`.
"""


def register_recipe(quantity: str, recipe: Recipe, *, first: bool = False) -> None:
    """Add a recipe for a quantity to :data:`RECIPES`.

    Args:
        quantity: Machine-readable name of the quantity the recipe computes.
        recipe: The recipe to add.
        first: Insert ahead of the existing recipes, so it is preferred among
            those with the same inputs available; otherwise append it as the
            last resort.

    Raises:
        KeyError: If ``quantity`` or any recipe input is not a quantity of the
            loaded ontology.
    """
    unknown = [name for name in (quantity, *recipe.inputs) if name not in COLUMN_ONTOLOGY]
    if unknown:
        raise KeyError(f"Not BDF quantities: {unknown}")
    recipes = RECIPES.setdefault(quantity, [])
    if first:
        recipes.insert(0, recipe)
    else:
        recipes.append(recipe)


# --------- Resolution ----------


def _parse_request(name: str) -> tuple[Quantity, str | None]:
    """Quantity and unit a requested name asks for, via :meth:`~bdf.spec.ColumnOntology.quantity_from_name`.

    Raises:
        BDFColumnError: If the name matches no current quantity, or names a
            unit that does not convert to the quantity's canonical unit.
    """
    parsed = COLUMN_ONTOLOGY.quantity_from_name(name)
    if parsed is not None:
        return parsed
    label = COLUMN_ONTOLOGY.quantity_from_label(name)
    if label is not None and not label[0].deprecated:
        reason = f"unit {label[1]!r} does not convert to {label[0].formatted_label!r}"
    else:
        reason = f"{name!r} is not a BDF preferred label or machine-readable name"
        if " / " not in name:
            reason += "; machine-readable names are fixed, so a unit is requested through a preferred label such as 'Voltage / mV'"
    raise BDFColumnError(f"Cannot parse column {name!r}: {reason}.")


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
            parsed = COLUMN_ONTOLOGY.quantity_from_name(column)
            if parsed is None:
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
        q, unit = _parse_request(name)
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
