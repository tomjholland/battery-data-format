"""Unit tests for bdf2.schema (BDFColumn, Syn, DateTimeSyn, FieldSpec, Normalizer)."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2.schema import (  # noqa: E402
    BDFColumn,
    DateTimeSyn,
    FieldSpec,
    Normalizer,
    Style,
    Syn,
    column,
)
from bdf2.sources import REGISTRY, get_normalizer

# --------------------------------------------------------------------- BDFColumn

def test_bdfcolumn_voltage_unit():
    assert BDFColumn.VOLTAGE_VOLT.unit == "V"


def test_bdfcolumn_voltage_label():
    assert BDFColumn.VOLTAGE_VOLT.label == "Voltage / V"


def test_bdfcolumn_iterable():
    members = list(BDFColumn)
    mr_names = [c.mr_name for c in members]
    assert "voltage_volt" in mr_names
    assert "current_ampere" in mr_names
    assert "test_time_second" in mr_names


def test_bdfcolumn_required_true():
    assert BDFColumn.VOLTAGE_VOLT.required is True
    assert BDFColumn.CURRENT_AMPERE.required is True
    assert BDFColumn.TEST_TIME_SECOND.required is True


def test_bdfcolumn_required_false():
    assert BDFColumn.AMBIENT_TEMPERATURE_CELSIUS.required is False
    assert BDFColumn.CYCLE_COUNT.required is False


def test_bdfcolumn_dtype():
    assert BDFColumn.CYCLE_COUNT.dtype == "int"
    assert BDFColumn.VOLTAGE_VOLT.dtype == "float"


# --------------------------------------------------------------------- Syn.parse

def test_syn_parse_brackets():
    s = Syn.parse("I[A]")
    assert s.qty == "I"
    assert s.style == Style.BRACKETS
    assert s.unit == "A"


def test_syn_parse_slash():
    s = Syn.parse("Ewe/V")
    assert s.qty == "Ewe"
    assert s.style == Style.SLASH
    assert s.unit == "V"


def test_syn_parse_parens():
    s = Syn.parse("current(mA)")
    assert s.qty == "current"
    assert s.style == Style.PARENS
    assert s.unit == "mA"


def test_syn_parse_none_unitless():
    s = Syn.parse("Cycle")
    assert s.qty == "Cycle"
    assert s.style == Style.NONE
    assert s.unit is None


def test_syn_parse_angle_brackets_qty():
    s = Syn.parse("<I>/A")
    assert s.qty == "<I>"
    assert s.style == Style.SLASH
    assert s.unit == "A"


# --------------------------------------------------------------------- DateTimeSyn

def test_datetime_syn_fields():
    d = DateTimeSyn(exemplar="Date", qty="Date", fmt="%d.%m.%Y %H:%M:%S")
    assert d.kind == "datetime"
    assert d.fmt == "%d.%m.%Y %H:%M:%S"


def test_syn_union_routes_datetime():
    from pydantic import TypeAdapter

    from bdf2.schema import SynUnion

    obj = {"kind": "datetime", "exemplar": "Date", "qty": "Date", "fmt": "%Y"}
    val = TypeAdapter(SynUnion).validate_python(obj)
    assert isinstance(val, DateTimeSyn)


def test_syn_union_routes_numeric():
    from pydantic import TypeAdapter

    from bdf2.schema import SynUnion

    obj = {"kind": "numeric", "exemplar": "Ewe/V", "qty": "Ewe", "style": "SLASH", "unit": "V"}
    val = TypeAdapter(SynUnion).validate_python(obj)
    assert isinstance(val, Syn)


# --------------------------------------------------------------------- column()

def test_column_parses_strings_to_syn():
    spec = column("Ewe/V", "Ecell/V")
    assert isinstance(spec, FieldSpec)
    assert len(spec.synonyms) == 2
    assert all(isinstance(s, Syn) for s in spec.synonyms)
    assert all(s.style == Style.SLASH for s in spec.synonyms)


def test_column_accepts_datetime_syn():
    dt = DateTimeSyn(exemplar="Date", qty="Date", fmt="%Y")
    spec = column(dt)
    assert isinstance(spec.synonyms[0], DateTimeSyn)


def test_column_mixed_args():
    dt = DateTimeSyn(exemplar="Date", qty="Date", fmt="%Y")
    spec = column("time/s", dt)
    assert isinstance(spec.synonyms[0], Syn)
    assert isinstance(spec.synonyms[1], DateTimeSyn)


# --------------------------------------------------------------------- Normalizer

def test_normalizer_construction():
    n = Normalizer(id="t", columns={BDFColumn.VOLTAGE_VOLT: column("Ewe/V")})
    assert n.id == "t"


def test_normalizer_empty_columns_rejected():
    with pytest.raises((ValueError, ValidationError, TypeError)):
        Normalizer(id="x", columns={})


def test_normalizer_incompatible_unit_rejected():
    with pytest.raises((ValueError, ValidationError, TypeError)):
        Normalizer(id="x", columns={BDFColumn.CURRENT_AMPERE: column("I[V]")})


def test_normalizer_compatible_unit_accepted():
    n = Normalizer(id="x", columns={BDFColumn.CURRENT_AMPERE: column("I[mA]")})
    assert n is not None


def test_normalizer_unitless_syn_unitless_col_accepted():
    n = Normalizer(id="x", columns={BDFColumn.CYCLE_COUNT: column("cycle number")})
    assert n is not None


def test_normalizer_datetime_syn_skips_unit_check():
    n = Normalizer(
        id="x",
        columns={
            BDFColumn.UNIX_TIME_SECOND: column(
                DateTimeSyn(exemplar="Date", qty="Date", fmt="%Y-%m-%d"),
            ),
        },
    )
    assert n is not None


# --------------------------------------------------------------------- resolve

def test_resolve_bracket_same_unit():
    n = Normalizer(id="x", columns={BDFColumn.CURRENT_AMPERE: column("I[A]")})
    rc = n.resolve(["I[A]"])[BDFColumn.CURRENT_AMPERE]
    assert rc.source_header == "I[A]"
    assert rc.scale == 1.0


def test_resolve_bracket_unit_scaled():
    n = Normalizer(id="x", columns={BDFColumn.CURRENT_AMPERE: column("I[A]")})
    rc = n.resolve(["I[mA]"])[BDFColumn.CURRENT_AMPERE]
    assert rc.source_header == "I[mA]"
    assert rc.scale == pytest.approx(0.001)


def test_resolve_style_mismatch_no_match():
    n = Normalizer(id="x", columns={BDFColumn.CURRENT_AMPERE: column("I[A]")})
    assert n.resolve(["I/A"]) == {}


def test_resolve_case_insensitive_qty():
    n = Normalizer(id="x", columns={BDFColumn.VOLTAGE_VOLT: column("Ewe/V")})
    assert BDFColumn.VOLTAGE_VOLT in n.resolve(["ewe/V"])


def test_resolve_unitless_match():
    n = Normalizer(id="x", columns={BDFColumn.CYCLE_COUNT: column("cycle number")})
    rc = n.resolve(["cycle number"])[BDFColumn.CYCLE_COUNT]
    assert rc.scale == 1.0


def test_resolve_datetime_syn():
    n = Normalizer(
        id="x",
        columns={
            BDFColumn.UNIX_TIME_SECOND: column(
                DateTimeSyn(exemplar="Date", qty="Date", fmt="%Y-%m-%d"),
            ),
        },
    )
    rc = n.resolve(["Date"])[BDFColumn.UNIX_TIME_SECOND]
    assert rc.datetime_fmt == "%Y-%m-%d"


def test_resolve_unmatched_absent():
    n = Normalizer(id="x", columns={BDFColumn.CURRENT_AMPERE: column("I[A]")})
    assert n.resolve(["voltage[V]"]) == {}


# --------------------------------------------------------------------- score, match_magic

def test_score_matches_count():
    n = Normalizer(
        id="x",
        columns={
            BDFColumn.VOLTAGE_VOLT: column("Ewe/V"),
            BDFColumn.CURRENT_AMPERE: column("I[A]"),
        },
    )
    assert n.score(["Ewe/V", "I[A]", "junk"]) == 2
    assert n.score(["nothing"]) == 0


def test_match_magic_substring():
    n = Normalizer(
        id="x", magic=("EC-Lab",),
        columns={BDFColumn.VOLTAGE_VOLT: column("Ewe/V")},
    )
    assert n.match_magic(b"abc EC-Lab Express def")


def test_match_magic_case_insensitive():
    n = Normalizer(
        id="x", magic=("BaSyTeC",),
        columns={BDFColumn.VOLTAGE_VOLT: column("Ewe/V")},
    )
    assert n.match_magic(b"basytec system")


def test_match_magic_no_match():
    n = Normalizer(
        id="x", magic=("EC-Lab",),
        columns={BDFColumn.VOLTAGE_VOLT: column("Ewe/V")},
    )
    assert not n.match_magic(b"random")


def test_match_magic_empty_returns_false():
    n = Normalizer(
        id="x", magic=(),
        columns={BDFColumn.VOLTAGE_VOLT: column("Ewe/V")},
    )
    assert not n.match_magic(b"any")


# --------------------------------------------------------------------- JSON round-trip

def test_normalizer_json_roundtrip():
    n = Normalizer(
        id="rt",
        magic=("foo",),
        columns={
            BDFColumn.VOLTAGE_VOLT: column("Ewe/V"),
            BDFColumn.UNIX_TIME_SECOND: column(
                DateTimeSyn(exemplar="Date", qty="Date", fmt="%Y"),
            ),
        },
    )
    s = n.model_dump_json()
    loaded = Normalizer.model_validate_json(s)
    assert loaded == n


def test_normalizer_json_unknown_column_rejected():
    with pytest.raises((ValueError, ValidationError, TypeError)):
        Normalizer.model_validate_json('{"id":"x","columns":{"NOT_A_COLUMN":{"synonyms":[]}}}')


# --------------------------------------------------------------------- REGISTRY

def test_registry_contains_builtins():
    ids = {n.id for n in REGISTRY}
    expected = {
        "arbin_csv", "basytec_txt", "biologic_mpt", "digatron_csv",
        "landt_csv", "landt_txt", "maccor_csv", "neware_csv", "novonix_csv",
    }
    assert expected.issubset(ids)


def test_registry_entries_normalizers():
    for n in REGISTRY:
        assert isinstance(n, Normalizer)


def test_get_normalizer_by_id():
    n = get_normalizer("biologic_mpt")
    assert n.id == "biologic_mpt"


def test_get_normalizer_passthrough():
    n = get_normalizer("biologic_mpt")
    assert get_normalizer(n) is n


def test_get_normalizer_unknown_raises():
    with pytest.raises(KeyError):
        get_normalizer("nonexistent_xyz")


# --------------------------------------------------------------------- required coverage

def test_every_source_covers_required_columns():
    """Each built-in source SHOULD have synonyms for required BDF columns it supports."""
    required = {c for c in BDFColumn if c.required}
    for n in REGISTRY:
        declared = set(n.columns.keys()) & required
        for col in declared:
            assert n.columns[col].synonyms, f"{n.id}: {col.name} has empty synonyms"
