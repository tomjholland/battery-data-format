"""Unit tests for bdf2._config."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2._config import (
    build_synonym_index,
    extract_qty_unit,
    get_datetime_index,
    get_source_regexes,
    get_synonym_index,
    load_config,
)


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
    """biologic_mpt '<ewe>/V' synonym → qty 'ewe' → voltage_volt."""
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
    synonym_index, _datetime_index = build_synonym_index(cfg)
    for sid in cfg["sources"]:
        assert sid in synonym_index
        assert isinstance(synonym_index[sid], dict)


def test_synonym_index_cached():
    """get_synonym_index returns same object on repeated calls."""
    idx1 = get_synonym_index()
    idx2 = get_synonym_index()
    assert idx1 is idx2


def test_synonym_unit_invariant():
    """Every synonym whose extracted unit is non-trivially different from the BDF
    canonical unit must be pint-equivalent to that canonical unit."""
    import pint
    from bdf2._normalize import _ureg

    cfg = load_config()
    source_regexes = get_source_regexes()
    failures = []

    for bdf_key, col_def in cfg["columns"].items():
        bdf_unit = col_def["unit"]
        if bdf_unit in ("1", ""):
            continue
        for source_id, synonyms in col_def.get("synonyms", {}).items():
            regexes = source_regexes.get(source_id, [])
            for synonym in synonyms:
                _, unit_str = extract_qty_unit(synonym, regexes)
                if unit_str is None:
                    continue
                # Case-insensitive string match — pint_factor returns 1.0 at runtime
                if unit_str.lower() == bdf_unit.lower():
                    continue
                try:
                    src = _ureg.parse_expression(unit_str)
                    tgt = _ureg.parse_expression(bdf_unit)
                    ratio = (src / tgt).to_base_units()
                    if not ratio.dimensionless:
                        failures.append(
                            f"{bdf_key}/{source_id}/{synonym!r}: "
                            f"unit {unit_str!r} not dimensionally equivalent to {bdf_unit!r}"
                        )
                except pint.errors.DimensionalityError:
                    failures.append(
                        f"{bdf_key}/{source_id}/{synonym!r}: "
                        f"unit {unit_str!r} not dimensionally equivalent to {bdf_unit!r}"
                    )
                except Exception:
                    # pint can't parse — skip (non-SI or unknown string)
                    pass

    assert not failures, "Synonym unit invariant violations:\n" + "\n".join(failures)


def test_datetime_synonyms_valid_format():
    """Every format string in datetime_synonyms must be parseable by Polars str.to_datetime."""
    import polars as pl

    cfg = load_config()
    sentinel_map = {
        "%Y-%m-%d %H:%M:%S%.f": "2024-01-01 00:00:00.123456",
        "%Y-%m-%d %H:%M:%S":    "2024-01-01 00:00:00",
        "%m/%d/%Y %H:%M:%S%.f": "01/01/2024 00:00:00.123456",
        "%m/%d/%Y %H:%M:%S":    "01/01/2024 00:00:00",
        "%d-%b-%y %I:%M:%S %p": "01-Jan-24 12:00:00 AM",
        "%d-%b-%y %H:%M:%S":    "01-Jan-24 00:00:00",
        "%Y/%m/%d %H:%M:%S":    "2024/01/01 00:00:00",
        "%Y-%m-%dT%H:%M:%S":    "2024-01-01T00:00:00",
    }

    failures = []
    for bdf_key, col_def in cfg["columns"].items():
        for source_id, dt_synonyms in col_def.get("datetime_synonyms", {}).items():
            for header, fmt in dt_synonyms.items():
                test_str = sentinel_map.get(fmt, "2024-01-01 00:00:00")
                try:
                    result = pl.Series([test_str]).str.to_datetime(fmt, strict=True)
                    if result[0] is None:
                        raise ValueError("parsed to null")
                except Exception as e:
                    failures.append(
                        f"{bdf_key}/{source_id}/{header!r}: "
                        f"format {fmt!r} failed with {e}"
                    )

    assert not failures, "Invalid datetime format strings:\n" + "\n".join(failures)


def test_pint_aliases_registered():
    """Units registered via _ureg.define() must be parseable by the registry."""
    from bdf2._normalize import _ureg
    import pint

    # These are the aliases hardcoded in _normalize.py
    aliases = ["degc", "degreec", "\xf8c"]
    failures = []
    for alias in aliases:
        try:
            _ureg.parse_expression(alias)
        except Exception as e:
            failures.append(f"{alias!r}: {e}")

    assert not failures, "Pint alias registration failures:\n" + "\n".join(failures)


def test_datetime_index_neware_date():
    """datetime_index should have 'date' → unix_time_second entry for neware_csv."""
    dt_index = get_datetime_index()
    assert "neware_csv" in dt_index
    neware = dt_index["neware_csv"]
    assert "date" in neware
    bdf_key, bdf_unit, fmt = neware["date"]
    assert bdf_key == "unix_time_second"
    assert bdf_unit == "s"
    assert fmt.startswith("%Y-%m-%d")


def test_datetime_index_cached():
    """get_datetime_index returns same object on repeated calls."""
    idx1 = get_datetime_index()
    idx2 = get_datetime_index()
    assert idx1 is idx2
