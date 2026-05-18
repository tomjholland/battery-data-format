"""Unit tests for bdf2.schema (Syn, DateTimeSyn, Normalizer, Source, MetadataParser)."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bdf2.schema import (  # noqa: E402
    DateTimeSyn,
    MetadataParser,
    Normalizer,
    ResolvedColumn,
    Source,
    Syn,
    _SPEC_COLUMNS,
    _col_dtype,
    _col_required,
    _col_unit,
    _match_header,
)
from bdf2.sources import REGISTRY, get_normalizer

# --------------------------------------------------------------------- column spec lookups

def test_spec_voltage_unit():
    assert _col_unit("voltage_volt") == "V"


def test_spec_voltage_label():
    assert _SPEC_COLUMNS["voltage_volt"]["label_template"].format(unit="V") == "Voltage / V"


def test_spec_mr_names_present():
    assert "voltage_volt" in _SPEC_COLUMNS
    assert "current_ampere" in _SPEC_COLUMNS
    assert "test_time_second" in _SPEC_COLUMNS


def test_spec_required_true():
    assert _col_required("voltage_volt") is True
    assert _col_required("current_ampere") is True
    assert _col_required("test_time_second") is True


def test_spec_required_false():
    assert _col_required("ambient_temperature_celsius") is False
    assert _col_required("cycle_count") is False


def test_spec_dtype():
    assert _col_dtype("cycle_count") == "int"
    assert _col_dtype("voltage_volt") == "float"


# --------------------------------------------------------------------- Syn construction and match

def test_syn_construction():
    s = Syn("I[{unit}]")
    assert s.exemplar == "I[{unit}]"


def test_syn_match_brackets():
    assert Syn("I[{unit}]").match("I[mA]") == "mA"


def test_syn_match_parens_case_insensitive():
    assert Syn("voltage({unit})").match("Voltage(V)") == "V"


def test_syn_match_compound_unit():
    assert Syn("q charge/{unit}").match("q charge/mA.h") == "mA.h"


def test_syn_match_plain_exact():
    assert Syn("voltage").match("voltage") == ""


def test_syn_match_plain_case_insensitive():
    assert Syn("Cycle").match("cycle") == ""


def test_syn_match_plain_no_unit_header():
    assert Syn("voltage").match("voltage(mV)") is None


def test_syn_match_qty_mismatch():
    assert Syn("I[{unit}]").match("voltage[V]") is None


def test_syn_match_missing_unit():
    assert Syn("I[{unit}]").match("I") is None


# --------------------------------------------------------------------- DateTimeSyn

def test_datetime_syn_fields():
    d = DateTimeSyn(syn=Syn("Date"), fmts=("%d.%m.%Y %H:%M:%S",))
    assert d.syn.exemplar == "Date"
    assert d.fmts == ("%d.%m.%Y %H:%M:%S",)


def test_datetime_syn_multiple_fmts():
    d = DateTimeSyn(syn=Syn("date"), fmts=("%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S"))
    assert len(d.fmts) == 2


def test_syn_union_routes_datetime():
    from pydantic import TypeAdapter

    from bdf2.schema import SynUnion

    obj = {"syn": "Date", "fmts": ["%Y"]}
    val = TypeAdapter(SynUnion).validate_python(obj)
    assert isinstance(val, DateTimeSyn)


def test_syn_union_routes_numeric():
    from pydantic import TypeAdapter

    from bdf2.schema import SynUnion

    val = TypeAdapter(SynUnion).validate_python("Ewe/{unit}")
    assert isinstance(val, Syn)
    assert val.exemplar == "Ewe/{unit}"


# --------------------------------------------------------------------- MetadataParser

def test_metadata_parser_construction():
    p = MetadataParser(start_time=r"Start:\s*(.+)")
    assert p.start_time == r"Start:\s*(.+)"


def test_metadata_parser_default_none():
    p = MetadataParser()
    assert p.start_time is None


def test_metadata_parser_parse_match():
    p = MetadataParser(start_time=r"Start:\s*(.+)")
    result = p.parse(["Start: 2024-01-15 09:00:00"])
    assert result == {"start_time": "2024-01-15 09:00:00"}


def test_metadata_parser_parse_no_match():
    p = MetadataParser(start_time=r"Start:\s*(.+)")
    result = p.parse(["unrelated line"])
    assert result == {}


def test_metadata_parser_parse_none_field():
    p = MetadataParser()
    result = p.parse(["Start: 2024-01-15"])
    assert result == {}


def test_metadata_parser_parse_first_match_wins():
    p = MetadataParser(start_time=r"Start:\s*(.+)")
    result = p.parse(["Start: first", "Start: second"])
    assert result == {"start_time": "first"}


# --------------------------------------------------------------------- Normalizer

def test_normalizer_empty_is_valid():
    n = Normalizer()
    assert all(getattr(n, mr) is None for mr in Normalizer.model_fields)


def test_normalizer_syn_field_construction():
    n = Normalizer(voltage_volt=[Syn("Ewe/{unit}"), Syn("U/{unit}")])
    assert len(n.voltage_volt) == 2  # type: ignore[arg-type]
    assert all(isinstance(s, Syn) for s in n.voltage_volt)  # type: ignore[union-attr]


def test_normalizer_resolved_column_field():
    rc = ResolvedColumn(source_header="U", bdf_unit="V", scale=1.0)
    n = Normalizer(voltage_volt=rc)
    assert n.voltage_volt == rc


def test_normalizer_compatible_unit_accepted():
    n = Normalizer(voltage_volt=[Syn("U[{unit}]")])
    assert n is not None


def test_normalizer_datetime_syn_skips_unit_check():
    n = Normalizer(
        unix_time_second=[DateTimeSyn(syn=Syn("Date"), fmts=("%Y-%m-%d",))],
    )
    assert n is not None


# --------------------------------------------------------------------- Normalizer.__iter__

def test_normalizer_iter_skips_none():
    n = Normalizer(voltage_volt=[Syn("U/{unit}")])
    pairs = list(n)
    assert len(pairs) == 1
    assert pairs[0][0] == "voltage_volt"


def test_normalizer_iter_declaration_order():
    n = Normalizer(
        current_ampere=[Syn("I[{unit}]")],
        voltage_volt=[Syn("Ewe/{unit}")],
    )
    cols = [mr_name for mr_name, _ in n]
    assert cols.index("voltage_volt") < cols.index("current_ampere")


def test_normalizer_iter_empty():
    n = Normalizer()
    assert list(n) == []


# --------------------------------------------------------------------- Normalizer.score

def test_normalizer_score_matches_count():
    n = Normalizer(
        voltage_volt=[Syn("Ewe/{unit}")],
        current_ampere=[Syn("I[{unit}]")],
    )
    assert n.score(["Ewe/V", "I[A]", "junk"]) == 2
    assert n.score(["nothing"]) == 0


def test_normalizer_score_resolved_column_skipped():
    rc = ResolvedColumn(source_header="U", bdf_unit="V", scale=1.0)
    n = Normalizer(voltage_volt=rc)
    # ResolvedColumn fields don't count in scoring
    assert n.score(["U"]) == 0


# --------------------------------------------------------------------- Normalizer.normalize

def test_normalizer_normalize_syn_path():
    import polars as pl
    n = Normalizer(voltage_volt=[Syn("Ewe/{unit}")])
    df = pl.DataFrame({"Ewe/V": ["3.5", "3.6"]})
    out, meta = n.normalize(df)
    assert "voltage_volt" in out.columns
    assert out["voltage_volt"][0] == pytest.approx(3.5)


def test_normalizer_normalize_resolved_column_path():
    import polars as pl
    rc = ResolvedColumn(source_header="U", bdf_unit="V", scale=1.0)
    n = Normalizer(voltage_volt=rc)
    df = pl.DataFrame({"U": [3.5, 3.6]})
    out, meta = n.normalize(df)
    assert "voltage_volt" in out.columns
    assert out["voltage_volt"][0] == pytest.approx(3.5)


def test_normalizer_normalize_none_field_absent():
    import polars as pl
    n = Normalizer()
    df = pl.DataFrame({"Ewe/V": ["3.5"]})
    out, meta = n.normalize(df)
    assert "voltage_volt" not in out.columns


def test_normalizer_normalize_include_optional_false():
    import polars as pl
    n = Normalizer(
        voltage_volt=[Syn("Ewe/{unit}")],
        cycle_count=[Syn("cycle number")],
    )
    df = pl.DataFrame({"Ewe/V": ["3.5"], "cycle number": ["3"]})
    out, _ = n.normalize(df, include_optional=False)
    assert "voltage_volt" in out.columns
    assert "cycle_count" not in out.columns


def test_normalizer_normalize_unit_conversion():
    import polars as pl
    n = Normalizer(current_ampere=[Syn("I/{unit}")])
    df = pl.DataFrame({"I/mA": ["1000.0"]})
    out, _ = n.normalize(df)
    assert out["current_ampere"][0] == pytest.approx(1.0)


# --------------------------------------------------------------------- _match_header end-to-end

def test_match_header_unit_scale():
    rc = _match_header("I[mA]", "I[mA]", "A", [Syn("I[{unit}]")])
    assert rc is not None
    assert rc.scale == pytest.approx(0.001)
    assert rc.source_header == "I[mA]"


# --------------------------------------------------------------------- Source

def test_source_construction():
    s = Source(
        id="test",
        normalizer=Normalizer(voltage_volt=[Syn("Ewe/{unit}")]),
    )
    assert s.id == "test"


def test_source_score_delegates_to_normalizer():
    s = Source(
        id="x",
        normalizer=Normalizer(
            voltage_volt=[Syn("Ewe/{unit}")],
            current_ampere=[Syn("I[{unit}]")],
        ),
    )
    assert s.score(["Ewe/V", "I[A]", "junk"]) == 2
    assert s.score(["nothing"]) == 0


def test_source_match_magic_substring():
    s = Source(
        id="x",
        magic=("EC-Lab",),
        normalizer=Normalizer(voltage_volt=[Syn("Ewe/{unit}")]),
    )
    assert s.match_magic(b"abc EC-Lab Express def")


def test_source_match_magic_case_insensitive():
    s = Source(
        id="x",
        magic=("BaSyTeC",),
        normalizer=Normalizer(voltage_volt=[Syn("Ewe/{unit}")]),
    )
    assert s.match_magic(b"basytec system")


def test_source_match_magic_no_match():
    s = Source(
        id="x",
        magic=("EC-Lab",),
        normalizer=Normalizer(voltage_volt=[Syn("Ewe/{unit}")]),
    )
    assert not s.match_magic(b"random")


def test_source_match_magic_empty_returns_false():
    s = Source(
        id="x",
        normalizer=Normalizer(voltage_volt=[Syn("Ewe/{unit}")]),
    )
    assert not s.match_magic(b"any")


# --------------------------------------------------------------------- REGISTRY

def test_registry_contains_builtins():
    ids = {n.id for n in REGISTRY}
    expected = {
        "arbin_csv", "basytec_txt", "biologic_mpt", "digatron_csv",
        "landt_csv", "landt_txt", "maccor_csv", "neware_csv", "novonix_csv",
    }
    assert expected.issubset(ids)


def test_registry_entries_are_sources():
    for n in REGISTRY:
        assert isinstance(n, Source)


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

def test_every_source_has_normalizer():
    for n in REGISTRY:
        assert isinstance(n.normalizer, Normalizer)
        assert any(True for _ in n.normalizer), f"{n.id}: normalizer has no defined fields"
