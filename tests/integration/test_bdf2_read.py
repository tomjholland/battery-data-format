"""End-to-end integration tests for bdf2.read()."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2 import read
from bdf2._config import load_config

SAMPLE_DATA = Path(__file__).parent.parent.parent / "sample_data"
BDF_LABELS = {v["label"] for v in load_config()["columns"].values()}


def _assert_bdf_output(df: pl.DataFrame, meta: dict, expected_source: str, expected_cols: list[str]):
    assert isinstance(df, pl.DataFrame)
    assert isinstance(meta, dict)
    assert "source" in meta

    if expected_source:
        assert meta["source"] == expected_source, f"Expected source {expected_source!r}, got {meta['source']!r}"

    for col in df.columns:
        if col in BDF_LABELS:
            dtype = df[col].dtype
            assert dtype in (pl.Float64, pl.Int64), f"BDF column {col!r} has unexpected dtype {dtype}"

    for col in expected_cols:
        assert col in df.columns, f"Expected column {col!r} in output"


# ---------------------------------------------------------------------------
# Per-source tests
# ---------------------------------------------------------------------------

def test_read_arbin_csv():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    df, meta = read(path, source="arbin_csv")
    _assert_bdf_output(df, meta, "arbin_csv", ["Test Time / s", "Voltage / V", "Current / A"])


def test_read_basytec_txt():
    path = SAMPLE_DATA / "basytec" / "sample_data_basytec.txt"
    df, meta = read(path, source="basytec_txt")
    _assert_bdf_output(df, meta, "basytec_txt", ["Test Time / s", "Voltage / V", "Current / A"])


def test_read_biologic_mpt():
    path = SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt"
    df, meta = read(path)
    print(df)
    assert 0==1
    _assert_bdf_output(df, meta, "biologic_mpt", ["Test Time / s", "Voltage / V"])
    assert df.shape[0] > 0


def test_read_biologic_no_header():
    """no_header file has column labels but no preamble — should still normalize."""
    path = SAMPLE_DATA / "biologic" / "Sample_data_biologic_no_header.mpt"
    df, meta = read(path)
    assert isinstance(df, pl.DataFrame)
    assert df.shape[0] > 0
    # Source may be detected from columns even without magic string
    assert meta["source"] in (None, "biologic_mpt")


def test_read_maccor_csv():
    path = SAMPLE_DATA / "maccor" / "sample_data_maccor.csv"
    df, meta = read(path)
    _assert_bdf_output(df, meta, "maccor_csv", ["Test Time / s", "Voltage / V", "Current / A"])


def test_read_neware_csv():
    path = SAMPLE_DATA / "neware" / "sample_data_neware.csv"
    df, meta = read(path, source="neware_csv")
    _assert_bdf_output(df, meta, "neware_csv", ["Voltage / V", "Current / A"])


def test_read_novonix_csv():
    path = SAMPLE_DATA / "novonix" / "sample_data_novonix.csv"
    df, meta = read(path)
    _assert_bdf_output(df, meta, "novonix_csv", ["Test Time / s", "Voltage / V", "Current / A"])


# ---------------------------------------------------------------------------
# lazy=True path
# ---------------------------------------------------------------------------

def test_read_lazy_returns_lazyframe():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    lf, meta = read(path, source="arbin_csv", lazy=True)
    assert isinstance(lf, pl.LazyFrame)
    df = lf.collect()
    assert "Test Time / s" in df.columns


# ---------------------------------------------------------------------------
# source override skips magic
# ---------------------------------------------------------------------------

def test_read_source_override():
    path = SAMPLE_DATA / "basytec" / "sample_data_basytec.txt"
    df, meta = read(path, source="basytec_txt")
    assert meta["source"] == "basytec_txt"


# ---------------------------------------------------------------------------
# metadata extraction
# ---------------------------------------------------------------------------

def test_read_basytec_metadata():
    path = SAMPLE_DATA / "basytec" / "sample_data_basytec.txt"
    _, meta = read(path, source="basytec_txt")
    # Basytec preamble has start/end datetime
    assert "start_datetime" in meta or "channel" in meta


def test_read_biologic_metadata():
    path = SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt"
    _, meta = read(path)
    # Biologic has 'acquisition_started' or 'channel' in preamble
    assert "acquisition_started" in meta or "channel" in meta


def test_read_novonix_metadata():
    path = SAMPLE_DATA / "novonix" / "sample_data_novonix.csv"
    _, meta = read(path)
    assert "channel" in meta


# ---------------------------------------------------------------------------
# shape / no empty output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,source", [
    (SAMPLE_DATA / "arbin" / "sample_data_arbin.csv", "arbin_csv"),
    (SAMPLE_DATA / "basytec" / "sample_data_basytec.txt", "basytec_txt"),
    (SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt", None),
    (SAMPLE_DATA / "novonix" / "sample_data_novonix.csv", None),
    (SAMPLE_DATA / "neware" / "sample_data_neware.csv", "neware_csv"),
    (SAMPLE_DATA / "maccor" / "sample_data_maccor.csv", None),
])
def test_read_non_empty(path, source):
    df, meta = read(path, source=source)
    assert df.shape[0] > 0, f"Empty DataFrame for {path.name}"
    assert df.shape[1] > 0
    assert isinstance(meta, dict)
