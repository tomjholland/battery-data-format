"""Unit tests for bdf2._config."""

import sys
from pathlib import Path

# Ensure bdf2 is importable from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2._config import build_synonym_index, get_synonym_index, load_config


def test_load_config_returns_dict():
    cfg = load_config()
    assert "columns" in cfg
    assert "sources" in cfg
    assert "datetime_formats" in cfg


def test_load_config_singleton():
    """columns.json is read from disk only once per process."""
    import bdf2._config as mod

    original = mod._config_cache
    try:
        mod._config_cache = None
        cfg1 = load_config()
        cfg2 = load_config()
        assert cfg1 is cfg2
    finally:
        mod._config_cache = original


def test_required_columns_present():
    cfg = load_config()
    for key in ("test_time_second", "voltage_volt", "current_ampere"):
        assert key in cfg["columns"], f"Missing required column: {key}"
        col = cfg["columns"][key]
        assert col["required"] is True


def test_column_structure():
    cfg = load_config()
    for key, col in cfg["columns"].items():
        assert "label" in col, f"{key} missing label"
        assert "unit" in col, f"{key} missing unit"
        assert "required" in col, f"{key} missing required"
        assert "synonyms" in col, f"{key} missing synonyms"


def test_source_structure():
    cfg = load_config()
    for sid, spec in cfg["sources"].items():
        assert "exts" in spec, f"{sid} missing exts"
        assert "magic" in spec, f"{sid} missing magic"
        assert "qty_unit_regexes" in spec, f"{sid} missing qty_unit_regexes"
        assert "decimal" in spec, f"{sid} missing decimal"


def test_synonym_index_quantity_extraction():
    """basytec_txt synonym 'time[s]' → qty key 'time' → (test_time_second, 's')."""
    index = get_synonym_index()
    assert "basytec_txt" in index
    bt = index["basytec_txt"]
    assert "time" in bt, f"Expected 'time' in basytec index, got: {list(bt.keys())[:10]}"
    bdf_key, bdf_unit = bt["time"]
    assert bdf_key == "test_time_second"
    assert bdf_unit == "s"


def test_synonym_index_voltage():
    index = get_synonym_index()
    bt = index["basytec_txt"]
    assert "u" in bt
    bdf_key, _ = bt["u"]
    assert bdf_key == "voltage_volt"


def test_synonym_index_full_string_fallback():
    """'cycle number' has no regex match → full string used as key."""
    index = get_synonym_index()
    bio = index["biologic_mpt"]
    assert "cycle number" in bio
    bdf_key, _ = bio["cycle number"]
    assert bdf_key == "cycle_count"


def test_synonym_index_angle_bracket():
    """biologic_mpt '<ewe>/v' synonym → qty 'ewe' → voltage_volt."""
    index = get_synonym_index()
    bio = index["biologic_mpt"]
    assert "ewe" in bio
    bdf_key, bdf_unit = bio["ewe"]
    assert bdf_key == "voltage_volt"
    assert bdf_unit == "V"


def test_synonym_index_landt_csv_unit_extraction():
    """landt_csv 'test_time_s' → regex extracts qty 'test_time' → test_time_second."""
    index = get_synonym_index()
    lc = index["landt_csv"]
    assert "test_time" in lc
    bdf_key, _ = lc["test_time"]
    assert bdf_key == "test_time_second"


def test_synonym_index_landt_csv_no_unit_fallback():
    """landt_csv 'cycle_index' doesn't match unit regex → falls back to 'cycle_index'."""
    index = get_synonym_index()
    lc = index["landt_csv"]
    assert "cycle_index" in lc
    bdf_key, _ = lc["cycle_index"]
    assert bdf_key == "cycle_count"


def test_build_synonym_index_returns_nested_dict():
    cfg = load_config()
    index = build_synonym_index(cfg)
    for sid in cfg["sources"]:
        assert sid in index
        assert isinstance(index[sid], dict)


def test_synonym_index_cached():
    """get_synonym_index returns same object on repeated calls."""
    idx1 = get_synonym_index()
    idx2 = get_synonym_index()
    assert idx1 is idx2
