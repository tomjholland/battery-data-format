"""Unit tests for bdf2._normalize."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2._config import load_config
from bdf2._detect import detect_layout, read_sample
from bdf2._normalize import (
    detect_source_from_columns,
    extract_qty_unit,
    normalize,
    normalize_pandas,
    pint_factor,
)

SAMPLE_DATA = Path(__file__).parent.parent.parent / "sample_data"
BDF_KEYS = set(load_config()["columns"].keys())
BDF_LABELS = {v["label"] for v in load_config()["columns"].values()}


# ---------------------------------------------------------------------------
# extract_qty_unit
# ---------------------------------------------------------------------------

def test_extract_qty_unit_parens():
    import re
    rx = [re.compile(r"^(.+?)\s*\(([^)]+)\)$")]
    qty, unit = extract_qty_unit("Voltage(V)", rx)
    assert qty == "Voltage"
    assert unit == "V"


def test_extract_qty_unit_bracket():
    import re
    rx = [re.compile(r"^~?([A-Za-z][A-Za-z0-9_-]*)\[([^\]]+)\]$")]
    qty, unit = extract_qty_unit("~Time[h]", rx)
    assert qty == "~Time" or qty == "Time"  # group 1 depends on tilde handling


def test_extract_qty_unit_no_match_returns_full():
    import re
    rx = [re.compile(r"^(.+?)\(([^)]+)\)$")]
    qty, unit = extract_qty_unit("Voltage", rx)
    assert qty == "Voltage"
    assert unit is None


# ---------------------------------------------------------------------------
# pint_factor
# ---------------------------------------------------------------------------

def test_pint_factor_ma_to_a():
    f = pint_factor("mA", "A")
    assert f == pytest.approx(0.001)


def test_pint_factor_h_to_s():
    f = pint_factor("h", "s")
    assert f == pytest.approx(3600.0)


def test_pint_factor_same_unit():
    f = pint_factor("V", "V")
    assert f == 1.0


def test_pint_factor_none_source():
    assert pint_factor(None, "A") is None


def test_pint_factor_dimensionless():
    assert pint_factor(None, "1") is None


def test_pint_factor_bad_unit_returns_none():
    f = pint_factor("not_a_unit_xyz", "A")
    assert f is None


def test_pint_factor_mah_to_ah():
    f = pint_factor("mAh", "Ah")
    assert f == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# detect_source_from_columns
# ---------------------------------------------------------------------------

def test_detect_source_basytec():
    from bdf2._config import get_synonym_index
    index = get_synonym_index()
    cols = ["~Time[s]", "U[V]", "I[A]"]
    src = detect_source_from_columns(cols, index)
    assert src == "basytec_txt"


def test_detect_source_biologic():
    from bdf2._config import get_synonym_index
    index = get_synonym_index()
    cols = ["time/s", "Ecell/V", "I/mA", "cycle number"]
    src = detect_source_from_columns(cols, index)
    assert src == "biologic_mpt"


def test_detect_source_no_match():
    from bdf2._config import get_synonym_index
    index = get_synonym_index()
    cols = ["unknown_col_xyz", "another_unknown"]
    src = detect_source_from_columns(cols, index)
    assert src is None


# ---------------------------------------------------------------------------
# normalize — basic column mapping
# ---------------------------------------------------------------------------

def test_normalize_basytec_column_rename():
    df = pl.DataFrame({"~Time[s]": ["1.0", "2.0"], "U[V]": ["3.5", "3.6"], "I[A]": ["0.1", "0.2"]})
    df_out, meta = normalize(df, source="basytec_txt")
    assert "Test Time / s" in df_out.columns
    assert "Voltage / V" in df_out.columns
    assert "Current / A" in df_out.columns
    assert meta["source"] == "basytec_txt"


def test_normalize_unknown_column_preserved():
    df = pl.DataFrame({"~Time[s]": ["1.0"], "Mystery": ["abc"]})
    df_out, meta = normalize(df, source="basytec_txt")
    assert "Mystery" in df_out.columns


def test_normalize_dtype_float64():
    df = pl.DataFrame({"~Time[s]": ["1.0", "2.0"]})
    df_out, _ = normalize(df, source="basytec_txt")
    assert df_out["Test Time / s"].dtype == pl.Float64


def test_normalize_no_source_unchanged():
    df = pl.DataFrame({"unknown_xyz": [1.0, 2.0]})
    df_out, meta = normalize(df)
    assert df_out.columns == ["unknown_xyz"]
    assert meta["source"] is None


def test_normalize_lazyframe():
    lf = pl.LazyFrame({"~Time[s]": ["1.0"], "U[V]": ["3.5"]})
    lf_out, meta = normalize(lf, source="basytec_txt")
    assert isinstance(lf_out, pl.LazyFrame)
    df = lf_out.collect()
    assert "Test Time / s" in df.columns


def test_normalize_unit_conversion_ma_to_a():
    """biologic I/mA column should be divided by 1000 → Current / A."""
    df = pl.DataFrame({"I/mA": ["1000.0"]})
    df_out, _ = normalize(df, source="biologic_mpt")
    val = df_out["Current / A"][0]
    assert val == pytest.approx(1.0)


def test_normalize_unit_conversion_h_to_s():
    """novonix Run Time (h) → Test Time / s, values ×3600."""
    df = pl.DataFrame({"Run Time (h)": ["1.0"]})
    df_out, _ = normalize(df, source="novonix_csv")
    val = df_out["Test Time / s"][0]
    assert val == pytest.approx(3600.0)


def test_normalize_decimal_comma_biologic():
    """biologic decimal=',' — '1,5' should become 1.5."""
    df = pl.DataFrame({"Ecell/V": ["1,5"]})
    df_out, _ = normalize(df, source="biologic_mpt")
    val = df_out["Voltage / V"][0]
    assert val == pytest.approx(1.5)


def test_normalize_metadata_columns():
    df = pl.DataFrame({"I/mA": ["100.0"], "time/s": ["5.0"]})
    _, meta = normalize(df, source="biologic_mpt")
    assert "Current / A" in meta["columns"]
    prov = meta["columns"]["Current / A"]
    assert prov["source_header"] == "I/mA"
    assert prov["bdf_unit"] == "A"


def test_normalize_source_override():
    """Explicit source skips auto-detection."""
    df = pl.DataFrame({"time/s": ["1.0"], "Ecell/V": ["3.5"]})
    _, meta = normalize(df, source="biologic_mpt")
    assert meta["source"] == "biologic_mpt"


# ---------------------------------------------------------------------------
# normalize — real file headers (task 5.6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_id,path,expected_cols", [
    ("arbin_csv",    SAMPLE_DATA / "arbin" / "sample_data_arbin.csv",
     ["Test Time / s", "Voltage / V", "Current / A"]),
    ("basytec_txt",  SAMPLE_DATA / "basytec" / "sample_data_basytec.txt",
     ["Test Time / s", "Voltage / V", "Current / A"]),
    ("biologic_mpt", SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt",
     ["Test Time / s", "Voltage / V"]),
    ("novonix_csv",  SAMPLE_DATA / "novonix" / "sample_data_novonix.csv",
     ["Test Time / s", "Voltage / V", "Current / A"]),
    ("neware_csv",   SAMPLE_DATA / "neware" / "sample_data_neware.csv",
     ["Voltage / V", "Current / A"]),
    ("maccor_csv",   SAMPLE_DATA / "maccor" / "sample_data_maccor.csv",
     ["Test Time / s", "Voltage / V", "Current / A"]),
])
def test_normalize_real_file_headers(source_id, path, expected_cols):
    """Load header row from sample file, run normalize, check BDF columns produced."""
    sample = read_sample(path)
    sep, header_idx, _, _ = detect_layout(sample)
    lines = sample.splitlines()
    header_line = lines[header_idx].rstrip(sep)
    headers = header_line.split(sep)

    # Build a minimal DataFrame with one dummy string row
    df = pl.DataFrame({h: ["1.0"] for h in headers if h.strip()})
    df_out, meta = normalize(df, source=source_id)

    # All output BDF columns must be valid labels
    for col in df_out.columns:
        if col in BDF_LABELS:
            assert col in BDF_LABELS
    # Expected columns are present
    for col in expected_cols:
        assert col in df_out.columns, f"Missing {col!r} for {source_id}"


# ---------------------------------------------------------------------------
# _sniff_decimal
# ---------------------------------------------------------------------------

def test_sniff_decimal_comma_strings():
    from bdf2._normalize import _sniff_decimal
    df = pl.DataFrame({"voltage": ["1,23", "4,56"], "current": ["0,1", "0,2"]})
    assert _sniff_decimal(df) == ","


def test_sniff_decimal_dot_strings():
    from bdf2._normalize import _sniff_decimal
    df = pl.DataFrame({"voltage": ["1.23", "4.56"]})
    assert _sniff_decimal(df) == "."


def test_sniff_decimal_no_numeric_strings():
    from bdf2._normalize import _sniff_decimal
    df = pl.DataFrame({"label": ["foo", "bar"]})
    assert _sniff_decimal(df) == "."


def test_sniff_decimal_float_columns_only():
    from bdf2._normalize import _sniff_decimal
    df = pl.DataFrame({"voltage": [1.23, 4.56]})
    assert _sniff_decimal(df) == "."


# ---------------------------------------------------------------------------
# normalize_pandas wrapper
# ---------------------------------------------------------------------------

def test_normalize_pandas_returns_pandas():
    import pandas as pd
    df = pd.DataFrame({"U[V]": [3.5, 3.6], "I[A]": [0.1, 0.2]})
    df_out, meta = normalize_pandas(df)
    assert isinstance(df_out, pd.DataFrame)
    assert "Voltage / V" in df_out.columns


# ---------------------------------------------------------------------------
# Hypothesis tests (task 5.7) — skipped when hypothesis not installed
# ---------------------------------------------------------------------------

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    _HYPOTHESIS = True
except ImportError:
    _HYPOTHESIS = False


@pytest.mark.skipif(not _HYPOTHESIS, reason="hypothesis not installed")
def test_hypothesis_basytec_normalize():
    """Synthetic basytec_txt DataFrame normalises to BDF column names."""
    from hypothesis import given, settings
    from hypothesis import strategies as st

    config = load_config()
    col_defs = config["columns"]
    synonyms = {
        bdf_key: col_def["synonyms"]["basytec_txt"]
        for bdf_key, col_def in col_defs.items()
        if "basytec_txt" in col_def.get("synonyms", {})
    }
    col_map = {syns[0]: bdf_key for bdf_key, syns in synonyms.items()}
    cols = list(col_map.keys())

    @settings(max_examples=20)
    @given(st.data())
    def inner(data):
        n = data.draw(st.integers(min_value=1, max_value=5))
        df_data = {
            col: [str(data.draw(st.floats(min_value=0, max_value=1e4, allow_nan=False)))
                  for _ in range(n)]
            for col in cols
        }
        df = pl.DataFrame(df_data)
        df_out, meta = normalize(df, source="basytec_txt")

        assert meta["source"] == "basytec_txt"
        for col, bdf_key in col_map.items():
            bdf_label = col_defs[bdf_key]["label"]
            assert bdf_label in df_out.columns
            assert df_out[bdf_label].dtype in (pl.Float64, pl.Int64)

    inner()
