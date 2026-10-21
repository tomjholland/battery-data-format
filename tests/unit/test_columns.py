from __future__ import annotations

import subprocess
import sys

import numpy as np
import pandas as pd
import polars as pl
import pytest

import bdf
from bdf.columns import ColumnName, can_resolve, parse_name, resolve, select, with_columns
from bdf.recipes import RECIPES, Recipe, register_recipe
from bdf.spec import COLUMN_ONTOLOGY


@pytest.fixture
def cycle() -> pl.DataFrame:
    """One hour charging at 1 A, then one hour discharging at 0.5 A, sampled every 10 s."""
    t = np.arange(0.0, 7200.0, 10.0)
    current = np.where(t < 3600, 1.0, -0.5)
    return pl.DataFrame(
        {
            "Test Time / s": t,
            "Current / A": current,
            "Voltage / V": np.full_like(t, 4.0),
            "Step Count / 1": (t >= 3600).astype(np.int64),
        }
    )


# --------- Name parsing ----------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Voltage / V", ColumnName("voltage_volt", "V")),
        ("voltage_volt", ColumnName("voltage_volt", "V")),
        ("Voltage / mV", ColumnName("voltage_volt", "mV")),
        ("Net Capacity / mAh", ColumnName("net_capacity_ah", "mAh")),
        ("net_capacity_ah", ColumnName("net_capacity_ah", "Ah")),
        ("Test Time / min", ColumnName("test_time_second", "min")),
        ("Applied Pressure / MPa", ColumnName("applied_pressure_pa", "MPa")),
        ("Ambient Temperature / K", ColumnName("ambient_temperature_celsius", "K")),
        ("Ambient Temperature / C", ColumnName("ambient_temperature_celsius", "degC")),
        ("ambient_temperature_celsius", ColumnName("ambient_temperature_celsius", "degC")),
        ("step_count", ColumnName("step_count", "1")),
        ("Step ID", ColumnName("step_id", None)),
        ("cycle_net_capacity_ah", ColumnName("cycle_net_capacity_ah", "Ah")),
    ],
)
def test_parse_name(name: str, expected: ColumnName) -> None:
    assert parse_name(name) == expected


@pytest.mark.parametrize(
    ("name", "match"),
    [
        # Machine-readable names are fixed identifiers: no unit conversion.
        ("voltage_mV", r"ask for 'voltage_volt', or a preferred label with a unit such as 'Voltage / <unit>'"),
        ("voltage_millivolt", "ask for 'voltage_volt'"),
        ("net_capacity_mah", "ask for 'net_capacity_ah'"),
        ("Voltage / A", "does not convert"),
        ("Foo / V", "not a BDF"),
        ("step_count_mA", "not a BDF"),
    ],
)
def test_parse_name_rejects(name: str, match: str) -> None:
    with pytest.raises(bdf.BDFColumnError, match=match):
        parse_name(name)


def test_every_quantity_parses_from_both_label_styles() -> None:
    for mr_name, q in COLUMN_ONTOLOGY:
        if q.deprecated:
            continue
        assert parse_name(q.formatted_label).quantity == mr_name
        assert parse_name(q.effective_notation).quantity == mr_name


# --------- Resolution ----------


def test_existing_column_passes_through(cycle: pl.DataFrame) -> None:
    out = with_columns(cycle, "Voltage / V")
    assert out.equals(cycle)


def test_recorded_column_converts_on_request(cycle: pl.DataFrame) -> None:
    out = select(cycle, "Voltage / mV", "Test Time / h", "Current / mA")
    assert out.columns == ["Voltage / mV", "Test Time / h", "Current / mA"]
    assert out.row(-1) == pytest.approx((4000.0, 7190.0 / 3600.0, -500.0))


def test_recorded_column_in_other_unit_converts_to_canonical() -> None:
    df = pl.DataFrame({"Voltage / mV": [3000.0], "Ambient Temperature / K": [300.0], "Current / A": [2.0]})
    out = select(df, "voltage_volt", "ambient_temperature_celsius", "power_watt")
    assert out.row(0) == pytest.approx((3.0, 300.0 - 273.15, 6.0))


def test_machine_readable_columns_in_the_data() -> None:
    df = pl.DataFrame({"voltage_volt": [3.0], "current_ampere": [2.0]})
    assert select(df, "Power / mW").item() == pytest.approx(6000.0)


def test_machine_readable_request_does_not_convert(cycle: pl.DataFrame) -> None:
    with pytest.raises(bdf.BDFColumnError, match="machine-readable names are fixed"):
        select(cycle, "voltage_mV")


def test_offset_conversion_from_canonical() -> None:
    df = pl.DataFrame({"Ambient Temperature / degC": [25.0]})
    assert select(df, "Ambient Temperature / K").item() == pytest.approx(298.15)


def test_power_from_current_and_voltage(cycle: pl.DataFrame) -> None:
    out = select(cycle, "Power / W", "Power / mW")
    assert out.row(0) == pytest.approx((4.0, 4000.0))


def test_net_capacity_integrates_current(cycle: pl.DataFrame) -> None:
    out = select(cycle, "net_capacity_ah", "charging_capacity_ah", "discharging_capacity_ah", "cumulative_capacity_ah")
    net, charge, discharge, cumulative = out.row(-1)
    # 3590 s at 1 A, then 3590 s at -0.5 A. Across the 10 s sign change the
    # clipped current falls 1 -> 0 A (charge) and rises 0 -> 0.5 A (discharge).
    expected_charge = (3590.0 + 10.0 * 0.5) / 3600.0
    expected_discharge = (3590.0 * 0.5 + 10.0 * 0.25) / 3600.0
    assert charge == pytest.approx(expected_charge)
    assert discharge == pytest.approx(expected_discharge)
    assert net == pytest.approx(expected_charge - expected_discharge)
    assert cumulative == pytest.approx(expected_charge + expected_discharge)


def test_recorded_inputs_beat_integration() -> None:
    # Recorded charge disagrees with the current on purpose, to show which recipe ran.
    df = pl.DataFrame(
        {
            "Test Time / s": [0.0, 3600.0],
            "Current / A": [1.0, 1.0],
            "Charging Capacity / Ah": [0.0, 5.0],
            "Discharging Capacity / Ah": [0.0, 2.0],
        }
    )
    assert select(df, "net_capacity_ah").item(-1, 0) == pytest.approx(3.0)


def test_step_scoped_quantities_restart_each_step(cycle: pl.DataFrame) -> None:
    out = select(cycle, "step_net_capacity_ah", "step_discharging_capacity_ah", "Step Time / s")
    first_of_step_1 = out.row(360)
    assert first_of_step_1 == pytest.approx((0.0, 0.0, 0.0))
    assert out.row(-1) == pytest.approx((-3590.0 * 0.5 / 3600.0, 3590.0 * 0.5 / 3600.0, 3590.0))


def test_step_charging_from_recorded_step_net(cycle: pl.DataFrame) -> None:
    recorded = select(cycle, "Test Time / s", "Step Count / 1", "step_net_capacity_ah")
    out = select(recorded, "step_charging_capacity_ah", "step_discharging_capacity_ah")
    assert out.row(359) == pytest.approx((3590.0 / 3600.0, 0.0))
    assert out.row(-1) == pytest.approx((0.0, 3590.0 * 0.5 / 3600.0))


def test_global_net_from_step_counters_adds_seams(cycle: pl.DataFrame) -> None:
    step_counters = select(
        cycle,
        "Test Time / s",
        "Current / A",
        "Step Count / 1",
        "step_charging_capacity_ah",
        "step_discharging_capacity_ah",
    )
    reconstructed = select(step_counters, "net_capacity_ah")
    integrated = select(cycle, "net_capacity_ah")
    # The step counters drop the boundary interval; the seam term restores it.
    np.testing.assert_allclose(reconstructed.to_series(), integrated.to_series(), atol=1e-12)


def test_test_time_and_step_count_recipes() -> None:
    df = pl.DataFrame({"Unix Time / s": [1000.0, 1010.0, 1030.0], "Step ID": [4, 4, 7]})
    out = select(df, "test_time_second", "step_count")
    assert out.rows() == [(0.0, 0), (10.0, 0), (30.0, 1)]


def test_impedance_recipes() -> None:
    df = pl.DataFrame({"Real Impedance / ohm": [3.0], "Imaginary Impedance / ohm": [-4.0]})
    modulus, phase = select(df, "absolute_impedance_ohm", "Phase / deg").row(0)
    assert modulus == pytest.approx(5.0)
    assert phase == pytest.approx(np.degrees(np.arctan2(-4.0, 3.0)))


def test_unresolvable_explains_recipes() -> None:
    with pytest.raises(bdf.BDFColumnError, match=r"(?s)'net_capacity_ah' is not in the data.*needs current_ampere"):
        select(pl.DataFrame({"Voltage / V": [1.0]}), "Net Capacity / mAh")


def test_circular_recipes_terminate() -> None:
    # net <-> cumulative <-> charging all refer to each other; none has a source.
    assert not can_resolve("cycle_cumulative_energy_wh", ["Voltage / V"])


def test_resolve_needs_only_names() -> None:
    exprs = resolve(["power_watt", "Voltage / mV"], ["Current / A", "Voltage / V"])
    assert list(exprs) == ["power_watt", "Voltage / mV"]
    assert all(isinstance(e, pl.Expr) for e in exprs.values())


# --------- Frame kinds ----------


@pytest.mark.parametrize("kind", ["polars", "lazy", "pandas"])
def test_same_kind_in_same_kind_out(cycle: pl.DataFrame, kind: str) -> None:
    frame = {"polars": cycle, "lazy": cycle.lazy(), "pandas": cycle.to_pandas()}[kind]
    out = with_columns(frame, ["power_watt", "net_capacity_ah"])
    assert type(out) is type(frame)
    columns = list(out.collect_schema().names()) if kind == "lazy" else list(out.columns)
    assert columns[-2:] == ["power_watt", "net_capacity_ah"]
    assert isinstance(select(frame, "power_watt"), type(frame))


def test_top_level_exports(cycle: pl.DataFrame) -> None:
    assert bdf.select(cycle, "power_watt").columns == ["power_watt"]
    assert isinstance(bdf.with_columns(cycle.to_pandas(), "power_watt"), pd.DataFrame)


# --------- Polars namespace ----------


def test_namespace_chains(cycle: pl.DataFrame) -> None:
    out = (
        cycle.bdf.with_columns("power_watt", "Net Capacity / mAh")
        .filter(pl.col("Step Count / 1") == 1)
        .group_by("Step Count / 1")
        .agg(pl.col("Net Capacity / mAh").min())
    )
    assert out.item(0, 1) == pytest.approx((3592.5 - 3590.0 * 0.5) / 3.6)


def test_namespace_on_lazyframe(cycle: pl.DataFrame) -> None:
    lazy = cycle.lazy().bdf.select("Test Time / h", "Voltage / mV")
    assert isinstance(lazy, pl.LazyFrame)
    assert lazy.collect().row(0) == (0.0, 4000.0)


def test_namespace_col(cycle: pl.DataFrame) -> None:
    discharging = cycle.filter(cycle.bdf.col("Current / mA") < 0)
    assert discharging.height == 360


def test_namespace_registered_when_polars_loaded_first() -> None:
    code = "import polars as pl, bdf; print(hasattr(pl.DataFrame(), 'bdf'))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "True"


# --------- Recipe registry ----------


# Schedule-scoped counters reset wherever the operator's schedule says, which
# the data does not record, so they can be used as inputs but never derived.
_UNDERIVABLE = {
    "schedule_charging_capacity_ah",
    "schedule_discharging_capacity_ah",
    "schedule_charging_energy_wh",
    "schedule_discharging_energy_wh",
}


def test_recipes_name_live_ontology_quantities() -> None:
    for target, recipes in RECIPES.items():
        assert target in COLUMN_ONTOLOGY and not COLUMN_ONTOLOGY[target].deprecated, target
        for recipe in recipes:
            for name in recipe.inputs:
                assert name in COLUMN_ONTOLOGY and not COLUMN_ONTOLOGY[name].deprecated, (target, name)


def test_every_ontology_derivation_has_a_recipe() -> None:
    derivable = {mr for mr, q in COLUMN_ONTOLOGY if q.derived_from and not q.deprecated}
    missing = derivable - _UNDERIVABLE - set(RECIPES)
    assert not missing, f"Ontology quantities with prov:wasDerivedFrom but no recipe: {sorted(missing)}"


def test_recipe_checks_arity() -> None:
    with pytest.raises(TypeError, match="2 positional"):
        Recipe(("current_ampere", "voltage_volt"), lambda i: i)


def test_register_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(RECIPES, "internal_resistance_ohm", [])
    register_recipe("internal_resistance_ohm", Recipe(("voltage_volt", "current_ampere"), lambda v, i: v / i))
    df = pl.DataFrame({"Voltage / V": [4.0], "Current / A": [2.0]})
    assert select(df, "Internal Resistance / mohm").item() == pytest.approx(2000.0)
    with pytest.raises(KeyError, match="not_a_quantity"):
        register_recipe("not_a_quantity", Recipe(("voltage_volt",), lambda v: v))


def test_global_from_step_scoped_adds_seams(cycle: pl.DataFrame) -> None:
    recorded = select(
        cycle, "Test Time / s", "Current / A", "Step Count / 1", "step_net_capacity_ah", "step_cumulative_capacity_ah"
    )
    net, cumulative = select(recorded, "net_capacity_ah", "cumulative_capacity_ah")
    integrated_net, integrated_cumulative = select(cycle, "net_capacity_ah", "cumulative_capacity_ah")
    np.testing.assert_allclose(net, integrated_net, atol=1e-12)
    # The boundary interval spans a sign change: the seam nets it to |1 - 0.5| / 2 * 10 s,
    # where integrating |I| gives (1 + 0.5) / 2 * 10 s.
    np.testing.assert_allclose(cumulative[-1], integrated_cumulative[-1] - 5.0 / 3600.0)
