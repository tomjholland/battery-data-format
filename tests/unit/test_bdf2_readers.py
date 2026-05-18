"""Unit tests for bdf2.readers (BaseReader, CSVReader, ExcelReader, MATReader)."""

import json
import sys
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2.readers import CSVReader, ExcelReader, MATReader  # noqa: E402
from bdf2.schema import BDFColumn, Normalizer, ResolvedColumn

SAMPLE_DATA = Path(__file__).parent.parent.parent / "sample_data"


# --------------------------------------------------------------------- CSVReader

def test_csvreader_defaults_none():
    r = CSVReader()
    assert r.separator is None
    assert r.skip_rows is None
    assert r.has_header is None
    assert r.decimal is None


def test_csvreader_source_string_coerced():
    r = CSVReader(source="biologic_mpt")
    assert isinstance(r.source, Normalizer)
    assert r.source.id == "biologic_mpt"


def test_csvreader_source_unknown_raises():
    with pytest.raises((ValueError, ValidationError, TypeError)):
        CSVReader(source="nope_xyz")


def test_csvreader_basytec_sniff():
    path = SAMPLE_DATA / "basytec" / "sample_data_basytec.txt"
    df, meta = CSVReader().read(path)
    assert meta["source"] == "basytec_txt"
    assert "voltage_volt" in df.columns


def test_csvreader_arbin_no_preamble():
    path = SAMPLE_DATA / "arbin" / "sample_data_arbin.csv"
    df, meta = CSVReader(source="arbin_csv").read(path)
    assert "test_time_second" in df.columns
    assert df.shape[0] > 0


def test_csvreader_explicit_separator():
    r = CSVReader(separator=",", source="arbin_csv")
    df, _ = r.read(SAMPLE_DATA / "arbin" / "sample_data_arbin.csv")
    assert df.shape[0] > 0


def test_csvreader_lazy():
    lf, _ = CSVReader(source="arbin_csv").read(
        SAMPLE_DATA / "arbin" / "sample_data_arbin.csv", lazy=True,
    )
    assert isinstance(lf, pl.LazyFrame)


def test_csvreader_from_config_file(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"separator": ";", "skip_rows": 2}))
    r = CSVReader.from_config_file(cfg)
    assert r.separator == ";"
    assert r.skip_rows == 2


def test_csvreader_sibling_config(tmp_path):
    data = tmp_path / "x.csv"
    data.write_text("a,b\n1,2\n3,4\n")
    cfg = tmp_path / "x.csv.json"
    cfg.write_text(json.dumps({"separator": ",", "skip_rows": 0}))
    r = CSVReader()
    r._fill_from_config_file(data)
    assert r.separator == ","


def test_csvreader_explicit_field_wins_over_config(tmp_path):
    data = tmp_path / "x.csv"
    data.write_text("a,b\n1,2\n3,4\n")
    cfg = tmp_path / "x.csv.json"
    cfg.write_text(json.dumps({"separator": ";"}))
    r = CSVReader(separator="|")
    r._fill_from_config_file(data)
    assert r.separator == "|"


def test_csvreader_from_config_file_bad_json(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not json")
    with pytest.raises(ValueError):
        CSVReader.from_config_file(cfg)


def test_contribution_walked(tmp_path):
    sub = tmp_path / "lab1"
    sub.mkdir()
    data = sub / "cell.csv"
    data.write_text("a,b\n1,2\n")
    (tmp_path / "contribution.json").write_text(
        json.dumps({"csv": {"separator": "|"}}),
    )
    r = CSVReader()
    r._fill_from_config_file(data)
    assert r.separator == "|"


# --------------------------------------------------------------------- ExcelReader

def test_excelreader_defaults():
    r = ExcelReader()
    assert r.sheet == 1
    assert r.engine == "calamine"
    assert r.has_header is True


def test_excelreader_smoke(tmp_path):
    pytest.importorskip("fastexcel")
    pytest.importorskip("xlsxwriter")
    import xlsxwriter
    p = tmp_path / "x.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    ws = wb.add_worksheet("Sheet1")
    ws.write_row(0, 0, ["voltage (V)", "current (A)"])
    ws.write_row(1, 0, [3.5, 0.1])
    ws.write_row(2, 0, [3.6, 0.2])
    wb.close()
    df, meta = ExcelReader(source="arbin_csv", sheet="Sheet1").read(p)
    assert "voltage_volt" in df.columns
    assert df.shape[0] == 2


# --------------------------------------------------------------------- MATReader

def test_matreader_empty_column_map_rejected():
    with pytest.raises((ValueError, ValidationError, TypeError)):
        MATReader(column_map={})


def test_matreader_string_shorthand_coerced():
    r = MATReader(column_map={BDFColumn.VOLTAGE_VOLT: "v_cell"})
    rc = r.column_map[BDFColumn.VOLTAGE_VOLT]
    assert isinstance(rc, ResolvedColumn)
    assert rc.source_header == "v_cell"
    assert rc.scale == 1.0
    assert rc.bdf_unit == "V"


def test_matreader_tuple2_coerced():
    r = MATReader(column_map={BDFColumn.CURRENT_AMPERE: ("i_meas", 0.001)})
    rc = r.column_map[BDFColumn.CURRENT_AMPERE]
    assert rc.scale == pytest.approx(0.001)
    assert rc.offset == 0.0


def test_matreader_tuple3_coerced():
    r = MATReader(column_map={
        BDFColumn.AMBIENT_TEMPERATURE_CELSIUS: ("temp_k", 1.0, -273.15),
    })
    rc = r.column_map[BDFColumn.AMBIENT_TEMPERATURE_CELSIUS]
    assert rc.offset == pytest.approx(-273.15)


def test_matreader_resolvedcolumn_direct():
    rc_in = ResolvedColumn(source_header="v", bdf_unit="V", scale=1.0, offset=0.0)
    r = MATReader(column_map={BDFColumn.VOLTAGE_VOLT: rc_in})
    assert r.column_map[BDFColumn.VOLTAGE_VOLT] == rc_in


def test_matreader_mismatched_bdf_unit_rejected():
    bad = ResolvedColumn(source_header="x", bdf_unit="A", scale=1.0, offset=0.0)
    with pytest.raises((ValueError, ValidationError, TypeError)):
        MATReader(column_map={BDFColumn.VOLTAGE_VOLT: bad})


def test_matreader_duplicate_source_header_rejected():
    with pytest.raises((ValueError, ValidationError, TypeError)):
        MATReader(column_map={
            BDFColumn.VOLTAGE_VOLT: "x",
            BDFColumn.CURRENT_AMPERE: "x",
        })


def test_matreader_read_smoke(tmp_path):
    scipy_io = pytest.importorskip("scipy.io")
    import numpy as np
    p = tmp_path / "x.mat"
    scipy_io.savemat(p, {"v": np.array([3.5, 3.6]), "i": np.array([1000.0, 2000.0])})
    r = MATReader(column_map={
        BDFColumn.VOLTAGE_VOLT: "v",
        BDFColumn.CURRENT_AMPERE: ("i", 0.001),
    })
    df, meta = r.read(p)
    assert df["voltage_volt"][0] == pytest.approx(3.5)
    assert df["current_ampere"][0] == pytest.approx(1.0)


def test_matreader_read_lazy(tmp_path):
    scipy_io = pytest.importorskip("scipy.io")
    import numpy as np
    p = tmp_path / "x.mat"
    scipy_io.savemat(p, {"v": np.array([3.5])})
    r = MATReader(column_map={BDFColumn.VOLTAGE_VOLT: "v"})
    lf, _ = r.read(p, lazy=True)
    assert isinstance(lf, pl.LazyFrame)
