"""Unit tests for bdf2._normalize against the new schema-driven API."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2 import normalize
from bdf2.readers import _sniff_decimal
from bdf2.schema import _SPEC_COLUMNS
from bdf2.sources import BIOLOGIC_MPT

MR_NAMES = set(_SPEC_COLUMNS.keys())


def test_normalize_basytec_rename():
    df = pl.DataFrame({
        "~Time[s]": ["1.0", "2.0"],
        "U[V]": ["3.5", "3.6"],
        "I[A]": ["0.1", "0.2"],
    })
    out = normalize(df, source="basytec_txt")
    assert set(out.columns) >= {"test_time_second", "voltage_volt", "current_ampere"}


def test_normalize_lazyframe_returns_lazyframe():
    lf = pl.LazyFrame({"~Time[s]": ["1.0"], "U[V]": ["3.5"]})
    out = normalize(lf, source="basytec_txt")
    assert isinstance(out, pl.LazyFrame)
    df = out.collect()
    assert "voltage_volt" in df.columns


def test_normalize_dataframe_returns_dataframe():
    df = pl.DataFrame({"~Time[s]": ["1.0"], "U[V]": ["3.5"]})
    out = normalize(df, source="basytec_txt")
    assert isinstance(out, pl.DataFrame)


def test_normalize_source_string_resolved():
    df = pl.DataFrame({"~Time[s]": ["1.0"], "U[V]": ["3.5"]})
    out = normalize(df, source="basytec_txt")
    assert "voltage_volt" in out.columns


def test_normalize_source_normalizer_instance():
    df = pl.DataFrame({"Ewe/V": ["3.5"]})
    out = normalize(df, source=BIOLOGIC_MPT)
    assert "voltage_volt" in out.columns


def test_normalize_unknown_source_raises():
    df = pl.DataFrame({"x": ["1.0"]})
    with pytest.raises(KeyError):
        normalize(df, source="nonexistent_xyz")


def test_normalize_autodetect():
    df = pl.DataFrame({"~Time[s]": ["1.0"], "U[V]": ["3.5"], "I[A]": ["0.1"]})
    out = normalize(df)
    assert set(out.columns) >= {"test_time_second", "voltage_volt", "current_ampere"}


def test_normalize_no_match_returns_unchanged():
    df = pl.DataFrame({"unknown_xyz": [1.0, 2.0]})
    out = normalize(df)
    assert out.columns == ["unknown_xyz"]


def test_normalize_unit_conversion_ma_to_a():
    df = pl.DataFrame({"I/mA": ["1000.0"]})
    out = normalize(df, source="biologic_mpt")
    assert out["current_ampere"][0] == pytest.approx(1.0)


def test_normalize_unit_conversion_h_to_s():
    df = pl.DataFrame({"Run Time (h)": ["1.0"]})
    out = normalize(df, source="novonix_csv")
    assert out["test_time_second"][0] == pytest.approx(3600.0)


def test_normalize_dtype_float64():
    df = pl.DataFrame({"~Time[s]": ["1.0", "2.0"]})
    out = normalize(df, source="basytec_txt")
    assert out["test_time_second"].dtype == pl.Float64


def test_normalize_dtype_int64_for_counts():
    df = pl.DataFrame({"cycle number": ["3"]})
    out = normalize(df, source="biologic_mpt")
    assert out["cycle_count"].dtype == pl.Int64


def test_normalize_decimal_comma():
    # Decimal coercion is the caller's responsibility; pre-process before normalize().
    raw = pl.DataFrame({"Ecell/V": ["1,5", "1,6"]})
    df = raw.with_columns(
        pl.col("Ecell/V").str.replace_all(",", ".", literal=True).cast(pl.Float64)
    )
    out = normalize(df, source="biologic_mpt")
    assert out["voltage_volt"][0] == pytest.approx(1.5)


def test_normalize_string_columns_cast_to_numeric():
    # normalize() casts string columns to Float64; already-numeric input works too.
    df = pl.DataFrame({"Ecell/V": ["1.5"]})
    out = normalize(df, source="biologic_mpt")
    assert out["voltage_volt"][0] == pytest.approx(1.5)


def test_normalize_column_map_valid_key():
    df = pl.DataFrame({
        "~Time[s]": ["1.0"],
        "my_volt": ["1000.0"],
        "I[A]": ["0.1"],
    })
    out = normalize(df, source="basytec_txt", column_map={"Voltage / mV": "my_volt"})
    assert "voltage_volt" in out.columns
    assert out["voltage_volt"][0] == pytest.approx(1.0)


def test_normalize_column_map_invalid_key_raises():
    df = pl.DataFrame({"~Time[s]": ["1.0"]})
    with pytest.raises(ValueError):
        normalize(df, source="basytec_txt", column_map={"NotReal / V": "col"})


def test_normalize_include_optional_false():
    df = pl.DataFrame({
        "time/s": ["1.0"], "Ecell/V": ["3.5"], "I/mA": ["100.0"], "cycle number": ["1"],
    })
    out = normalize(df, source="biologic_mpt", include_optional=False)
    assert "test_time_second" in out.columns
    assert "cycle_count" not in out.columns


def test_normalize_extra_columns_passthrough():
    df = pl.DataFrame({
        "time/s": ["1.0"], "Ecell/V": ["3.5"], "protocol_id": ["charge_cc"],
    })
    out = normalize(df, source="biologic_mpt", extra_columns={"protocol_id": "Protocol"})
    assert out["Protocol"][0] == "charge_cc"


def test_normalize_extra_columns_missing_warns():
    import warnings as w

    df = pl.DataFrame({"time/s": ["1.0"]})
    with w.catch_warnings(record=True) as caught:
        w.simplefilter("always")
        out = normalize(df, source="biologic_mpt", extra_columns={"ghost": "Out"})
    assert any(issubclass(c.category, UserWarning) for c in caught)
    assert "Out" not in out.columns


def test_sniff_decimal_comma():
    df = pl.DataFrame({"voltage": ["1,23", "4,56"]})
    assert _sniff_decimal(df) == ","


def test_sniff_decimal_dot():
    df = pl.DataFrame({"voltage": ["1.23"]})
    assert _sniff_decimal(df) == "."


def test_sniff_decimal_no_strings():
    df = pl.DataFrame({"v": [1.23]})
    assert _sniff_decimal(df) == "."
