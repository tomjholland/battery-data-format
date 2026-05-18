"""End-to-end integration tests for bdf2.read()."""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2 import CSVReader, read
from bdf2.schema import _SPEC_COLUMNS

SAMPLE_DATA = Path(__file__).parent.parent.parent / "sample_data"
MR_NAMES = set(_SPEC_COLUMNS.keys())


def _assert_bdf(df, meta, source_id, required_mr_names):
    assert isinstance(df, pl.DataFrame)
    assert meta["source"] == source_id
    for col in df.columns:
        if col in MR_NAMES:
            assert df[col].dtype in (pl.Float64, pl.Int64)
    for m in required_mr_names:
        assert m in df.columns, f"missing {m!r} for {source_id}"


def test_read_arbin_csv():
    df, meta = read(SAMPLE_DATA / "arbin" / "sample_data_arbin.csv", source="arbin_csv")
    _assert_bdf(df, meta, "arbin_csv", ["test_time_second", "voltage_volt", "current_ampere"])


def test_read_basytec_txt():
    df, meta = read(SAMPLE_DATA / "basytec" / "sample_data_basytec.txt", source="basytec_txt")
    _assert_bdf(df, meta, "basytec_txt", ["test_time_second", "voltage_volt", "current_ampere"])


def test_read_biologic_mpt_autodetect():
    df, meta = read(SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt")
    _assert_bdf(df, meta, "biologic_mpt", ["test_time_second", "voltage_volt"])
    assert df.shape[0] > 0


def test_read_biologic_no_header():
    df, meta = read(SAMPLE_DATA / "biologic" / "Sample_data_biologic_no_header.mpt")
    assert df.shape[0] > 0
    assert meta["source"] in (None, "biologic_mpt")


def test_read_maccor_csv():
    df, meta = read(SAMPLE_DATA / "maccor" / "sample_data_maccor.csv")
    _assert_bdf(df, meta, "maccor_csv", ["test_time_second", "voltage_volt", "current_ampere"])


def test_read_neware_csv():
    df, meta = read(SAMPLE_DATA / "neware" / "sample_data_neware.csv", source="neware_csv")
    _assert_bdf(df, meta, "neware_csv", ["voltage_volt", "current_ampere"])


def test_read_novonix_csv():
    df, meta = read(SAMPLE_DATA / "novonix" / "sample_data_novonix.csv")
    _assert_bdf(df, meta, "novonix_csv", ["test_time_second", "voltage_volt", "current_ampere"])


def test_read_lazy_returns_lazyframe():
    lf, _ = read(SAMPLE_DATA / "arbin" / "sample_data_arbin.csv", source="arbin_csv", lazy=True)
    assert isinstance(lf, pl.LazyFrame)
    assert "voltage_volt" in lf.collect_schema().names()


def test_read_basytec_preamble_metadata():
    _, meta = read(SAMPLE_DATA / "basytec" / "sample_data_basytec.txt", source="basytec_txt")
    assert "start_time" in meta


def test_read_biologic_preamble_metadata():
    _, meta = read(SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt")
    assert "start_time" in meta


@pytest.mark.parametrize("path", [
    SAMPLE_DATA / "arbin" / "sample_data_arbin.csv",
    SAMPLE_DATA / "basytec" / "sample_data_basytec.txt",
    SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt",
    SAMPLE_DATA / "novonix" / "sample_data_novonix.csv",
    SAMPLE_DATA / "neware" / "sample_data_neware.csv",
    SAMPLE_DATA / "maccor" / "sample_data_maccor.csv",
])
def test_read_non_empty(path):
    df, meta = read(path)
    assert df.shape[0] > 0
    assert df.shape[1] > 0
    assert isinstance(meta, dict)


def test_read_with_reader_instance_overrides_extension():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    df, meta = read(path, reader=CSVReader(source="arbin_csv"))
    assert meta["source"] == "arbin_csv"
    assert "voltage_volt" in df.columns


def test_read_mat_without_column_map_raises(tmp_path):
    p = tmp_path / "fake.mat"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="column_map"):
        read(p)


def test_read_include_optional_false():
    df, _ = read(
        SAMPLE_DATA / "biologic" / "Sample_data_biologic_01_MB_CA1.txt",
        include_optional=False,
    )
    for c in df.columns:
        if c in MR_NAMES:
            assert _SPEC_COLUMNS[c]["required"], c
