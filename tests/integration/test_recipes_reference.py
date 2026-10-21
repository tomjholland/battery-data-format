"""Recipes on real data: capacity derived from recorded counters agrees with integrated current.

Each reference file records capacity in a different scope (test, step,
schedule). Deriving ``net_capacity_ah`` uses those recorded counters; deriving
it from current and time alone integrates the sampled current. The two must
agree to within a small fraction of the capacity swing, or a recipe has picked
the wrong scope or mishandled the resets.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import bdf
from bdf.recipes import select

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "examples" / "reference"

# filename -> (recorded capacity scope, tolerance as a fraction of the net capacity swing)
CASES: dict[str, tuple[str, float]] = {
    "FZJ__INR21700__20250606__HPPC__25degC__Digatron.bdf.csv": ("test", 0.005),
    "SINTEF__NaCR32140-MP10-04__2025-08-25__GITT_0p05C_25degC__BioLogic.bdf.csv": ("test", 0.005),
    # The cycler's step counter and the 10 s-sampled current disagree by ~2% of throughput.
    "SINTEF__G20M7-202512-Gru6mV__20251228__C30__25degC__Neware.bdf.csv": ("step", 0.05),
    "shandong__nacr32140-mp10__2023-10-10__pulse__25degC__arbin.bdf.csv": ("schedule", 0.02),
}


@pytest.mark.parametrize(("filename", "scope", "tolerance"), [(f, *c) for f, c in CASES.items()], ids=list(CASES))
def test_recorded_capacity_matches_integrated_current(filename: str, scope: str, tolerance: float) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df, _ = bdf.read(REFERENCE_DIR / filename)
    prefix = "" if scope == "test" else f"{scope}_"
    assert any(c.startswith(f"{prefix.replace('_', ' ').title()}") and "Capacity" in c for c in df.columns)

    from_counters = select(df, "net_capacity_ah").to_series()
    from_current = select(df.select("Test Time / s", "Current / A"), "net_capacity_ah").to_series()

    swing = from_counters.max() - from_counters.min()
    assert (from_counters - from_current).abs().max() <= tolerance * swing
