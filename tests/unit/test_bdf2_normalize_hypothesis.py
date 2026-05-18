"""Hypothesis property tests for bdf2.normalize unit conversion."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st  # noqa: E402

from bdf2 import normalize  # noqa: E402


@settings(deadline=None, max_examples=50)
@given(values=st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), min_size=1, max_size=10))
def test_biologic_ma_to_a_property(values):
    df = pl.DataFrame({"I/mA": [str(v) for v in values]})
    out, _ = normalize(df, source="biologic_mpt")
    converted = out["current_ampere"].to_list()
    for got, src in zip(converted, values):
        assert got == pytest.approx(src / 1000.0, rel=1e-9, abs=1e-12)


@settings(deadline=None, max_examples=50)
@given(values=st.lists(st.floats(min_value=0, max_value=1000, allow_nan=False), min_size=1, max_size=10))
def test_novonix_h_to_s_property(values):
    df = pl.DataFrame({"Run Time (h)": [str(v) for v in values]})
    out, _ = normalize(df, source="novonix_csv")
    converted = out["test_time_second"].to_list()
    for got, src in zip(converted, values):
        assert got == pytest.approx(src * 3600.0, rel=1e-9, abs=1e-9)


@settings(deadline=None, max_examples=50)
@given(values=st.lists(st.floats(min_value=-1e3, max_value=1e3, allow_nan=False), min_size=1, max_size=10))
def test_basytec_v_to_v_property(values):
    df = pl.DataFrame({"U[V]": [str(v) for v in values]})
    out, _ = normalize(df, source="basytec_txt")
    for got, src in zip(out["voltage_volt"].to_list(), values):
        assert got == pytest.approx(src, rel=1e-9, abs=1e-12)
