"""Recipes that compute BDF quantities from other BDF quantities.

A :class:`Recipe` names the quantities it needs (by machine-readable name) and
a function that turns their Polars expressions into the target quantity.
:data:`RECIPES` maps each derivable quantity to its recipes, most trustworthy
first. :mod:`bdf.columns` walks this registry to resolve a requested column
that is not in the data.

Every expression a recipe receives is in the quantity's canonical BDF unit
(``current_ampere`` in A, ``test_time_second`` in s, ...), and every recipe
returns its target in the target's canonical unit. Unit conversion happens
outside the recipes, so a recipe never deals with units.

The quantities and their relationships come from the ontology
(``prov:wasDerivedFrom`` and ``latexFormula``). The ontology says *what* a
derived quantity is; the recipes here say *how* to compute it from sampled
data, including fallbacks for when the ideal inputs are missing.

Recipes that accumulate (``cum_sum``, ``diff``) assume rows are in time order
for one test. Derive before filtering rows, not after. Scoped recipes assume
step and cycle counts never decrease, as the ontology defines them.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable

import polars as pl

__all__ = ["RECIPES", "Recipe", "register_recipe"]


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
    from bdf.spec import COLUMN_ONTOLOGY

    unknown = [name for name in (quantity, *recipe.inputs) if name not in COLUMN_ONTOLOGY]
    if unknown:
        raise KeyError(f"Not BDF quantities: {unknown}")
    recipes = RECIPES.setdefault(quantity, [])
    if first:
        recipes.insert(0, recipe)
    else:
        recipes.append(recipe)
