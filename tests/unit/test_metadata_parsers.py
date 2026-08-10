"""Unit tests for bdf.metadata_parsers (MetadataRules and parser classes)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import bdf.metadata_parsers
from bdf._datetime_fmt import split_tz_fmts, to_epoch_seconds
from bdf.battinfo_records import TestSection
from bdf.metadata_parsers import (
    JsonSidecarParser,
    MetadataParser,
    MetadataRules,
    TxtPreambleParser,
    _coerce_preamble_datetimes,
)

INSTRUMENT_RX = re.compile(r"~Instrument:\s*(.+)")


def _parsed_fields(result: dict[str, str] | BaseModel) -> dict[str, str]:
    """Return a parse() result's set fields as a plain dict.

    Args:
        result: A parse() return value: either a dict or a pydantic model.

    Returns:
        Mapping of field name to value for every field the parser set.
    """
    if isinstance(result, BaseModel):
        return result.model_dump(exclude_unset=True)
    return dict(result)


class TestMetadataRules:
    def test_rules_rejects_unknown_field(self) -> None:
        """extra='forbid' rejects unknown metadata field names at construction."""
        with pytest.raises(ValidationError):
            MetadataRules(unknown_field="x")  # type: ignore[call-arg]

    def test_rules_is_hashable(self) -> None:
        """A MetadataRules instance can be placed in a frozenset."""
        s: MetadataRules[str] = MetadataRules(started_at="x")
        assert s in frozenset({s})

    def test_rules_iter_yields_only_set_fields(self) -> None:
        """Iterating yields (field_name, rule) only for set fields."""
        s = MetadataRules[str](started_at=r"X:(.+)")
        assert list(s) == [("started_at", r"X:(.+)")]
        assert list(MetadataRules[str]()) == []

    def test_rules_pattern_compiles_string_input(self) -> None:
        """Pydantic coerces a str to re.Pattern when T=re.Pattern[str]."""
        s = MetadataRules[re.Pattern[str]](started_at="X:(.+)")  # type: ignore[arg-type]
        _, pattern = next(iter(s))
        assert isinstance(pattern, re.Pattern)
        assert pattern == re.compile("X:(.+)")

    def test_extract_applies_matcher_to_set_fields(self) -> None:
        """extract() calls match_one per set field and keeps non-None results."""
        s = MetadataRules[str](started_at="rule")
        seen: list[str] = []

        def match_one(rule: str) -> str:
            seen.append(rule)
            return f"value:{rule}"

        assert s.extract(match_one) == {"started_at": "value:rule"}
        assert seen == ["rule"]

    def test_extract_skips_fields_when_matcher_returns_none(self) -> None:
        """A field whose matcher returns None is omitted from the result."""
        s = MetadataRules[str](started_at="rule")
        assert s.extract(lambda _rule: None) == {}

    def test_extract_skips_unset_fields(self) -> None:
        """extract() never invokes the matcher for unset (None) fields."""
        calls = 0

        def match_one(_rule: str) -> str:
            nonlocal calls
            calls += 1
            return "x"

        assert MetadataRules[str]().extract(match_one) == {}
        assert calls == 0

    def test_rules_has_no_metadata_schema_alias(self) -> None:
        """No `MetadataSchema` alias is exported from bdf.metadata_parsers."""
        assert not hasattr(bdf.metadata_parsers, "MetadataSchema")

    def test_rules_fields_are_declared_on_test_section(self) -> None:
        """Every MetadataRules field name is a declared field of the test-section model."""
        assert set(MetadataRules.model_fields) <= set(TestSection.model_fields)


class TestMetadataParserBase:
    """MetadataParser base (null case)."""

    def test_base_never_matches(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("anything")
        assert MetadataParser().matches(p) is False

    def test_base_parse_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("anything")
        assert _parsed_fields(MetadataParser().parse(p)) == {}

    def test_base_is_hashable(self) -> None:
        assert MetadataParser() in frozenset({MetadataParser()})


class TestTxtPreambleParser:
    def test_txt_matches_true_when_magic_present(self, tmp_path: Path) -> None:
        p = tmp_path / "basytec.txt"
        p.write_text("ResultFile from BaSyTec Battery Test System\n~Start of Test: 01.01.2024\n")
        assert TxtPreambleParser(magic=("basytec battery test system",)).matches(p) is True

    def test_txt_matches_false_when_magic_absent(self, tmp_path: Path) -> None:
        p = tmp_path / "biologic.txt"
        p.write_text("BT-Lab ASCII FILE\n")
        assert TxtPreambleParser(magic=("basytec battery test system",)).matches(p) is False

    def test_txt_matches_bytes_token(self, tmp_path: Path) -> None:
        p = tmp_path / "raw.bin"
        p.write_bytes(b"\x00\x01\x02 data")
        assert TxtPreambleParser(magic=(b"\x00\x01",)).matches(p) is True

    def test_txt_matches_empty_magic_false(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("anything")
        assert TxtPreambleParser().matches(p) is False

    def test_txt_parse_extracts_field(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("header\n~Instrument: Maccor 4000\nmore\n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](instrument_name=INSTRUMENT_RX))
        assert _parsed_fields(parser.parse(p)) == {"instrument_name": "Maccor 4000"}

    def test_txt_parse_honours_encoding(self, tmp_path: Path) -> None:
        """parse() decodes the head with the configured encoding (latin-1)."""
        p = tmp_path / "latin1.txt"
        p.write_bytes("~Instrument: caf\xe9\n".encode("latin-1"))
        parser = TxtPreambleParser(
            encoding="latin-1",
            regex_patterns=MetadataRules[re.Pattern[str]](instrument_name=INSTRUMENT_RX),
        )
        assert _parsed_fields(parser.parse(p)) == {"instrument_name": "caf\xe9"}

    def test_txt_parse_returns_only_matched_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("no relevant lines here\n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](instrument_name=INSTRUMENT_RX))
        assert _parsed_fields(parser.parse(p)) == {}

    def test_txt_parse_strips_captured_value(self, tmp_path: Path) -> None:
        """match_one strips surrounding whitespace from group(1)."""
        p = tmp_path / "f.txt"
        p.write_text("~Instrument:   Maccor 4000   \n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](instrument_name=INSTRUMENT_RX))
        assert _parsed_fields(parser.parse(p)) == {"instrument_name": "Maccor 4000"}

    def test_txt_parse_first_matching_line_wins(self, tmp_path: Path) -> None:
        """match_one returns the first matching line, ignoring later matches."""
        p = tmp_path / "f.txt"
        p.write_text("~Instrument: first\n~Instrument: second\n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](instrument_name=INSTRUMENT_RX))
        assert _parsed_fields(parser.parse(p)) == {"instrument_name": "first"}

    def test_txt_is_hashable(self) -> None:
        parser = TxtPreambleParser(
            magic=("x",),
            regex_patterns=MetadataRules[re.Pattern[str]](instrument_name=re.compile(r"a(.+)")),
        )
        assert parser in frozenset({parser})

    def test_txt_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            TxtPreambleParser(bogus="x")  # type: ignore[call-arg]


class TestTxtPreambleDatetimeCoercion:
    """`TxtPreambleParser.datetime_formats` coerces matched datetimes to epoch seconds."""

    def test_txt_datetime_naive_localised_to_epoch(self, tmp_path: Path) -> None:
        """A naive matched datetime is localised to tz, then coerced to int epoch seconds.

        Two candidate formats are given, with the matching one second in the tuple,
        so an implementation that only tries the first format also fails this test.
        """
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test: 01.01.2024 10:00:00\n")
        parser = TxtPreambleParser(
            regex_patterns=MetadataRules[re.Pattern[str]](started_at=re.compile(r"~Start of Test:\s*(.+)")),
            datetime_formats=("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"),
        )
        result = _parsed_fields(parser.parse(p, tz="Europe/Berlin"))  # type: ignore[call-arg]
        assert result["started_at"] == 1704099600
        assert isinstance(result["started_at"], int)

    def test_txt_datetime_offset_bearing_keeps_its_own_offset(self, tmp_path: Path) -> None:
        """A matched datetime carrying its own UTC offset ignores the tz argument."""
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test: 2024-01-01T10:00:00+02:00\n")
        parser = TxtPreambleParser(
            regex_patterns=MetadataRules[re.Pattern[str]](started_at=re.compile(r"~Start of Test:\s*(.+)")),
            datetime_formats=("%Y-%m-%dT%H:%M:%S%z",),
        )
        result = _parsed_fields(parser.parse(p, tz="UTC"))  # type: ignore[call-arg]
        assert result["started_at"] == 1704096000

    def test_txt_datetime_unparseable_text_leaves_field_unset(self, tmp_path: Path) -> None:
        """Matched text no candidate format parses leaves the field unset, not an error."""
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test: not-a-date\n")
        parser = TxtPreambleParser(
            regex_patterns=MetadataRules[re.Pattern[str]](started_at=re.compile(r"~Start of Test:\s*(.+)")),
            datetime_formats=("%d.%m.%Y %H:%M:%S",),
        )
        result = _parsed_fields(parser.parse(p, tz="UTC"))  # type: ignore[call-arg]
        assert "started_at" not in result

    def test_txt_datetime_epoch_digit_passthrough_only_from_real_int_source(self, tmp_path: Path) -> None:
        """Integer passthrough applies only where the source yields a real int (JSON sidecar).

        On the preamble path all matched text is a string; a bare epoch-digit string
        that no declared format parses leaves the field unset, the same as any other
        unparseable text.
        """
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"started_at": 1704106800}))
        sidecar_parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at",)))
        sidecar_result = _parsed_fields(sidecar_parser.parse(data))
        assert sidecar_result["started_at"] == 1704106800
        assert isinstance(sidecar_result["started_at"], int)

        p = tmp_path / "f.txt"
        p.write_text("~Start of Test: 1704106800\n")
        preamble_parser = TxtPreambleParser(
            regex_patterns=MetadataRules[re.Pattern[str]](started_at=re.compile(r"~Start of Test:\s*(.+)")),
            datetime_formats=("%d.%m.%Y %H:%M:%S",),
        )
        preamble_result = _parsed_fields(preamble_parser.parse(p, tz="UTC"))  # type: ignore[call-arg]
        assert "started_at" not in preamble_result

    @pytest.mark.parametrize("field", ["started_at", "ended_at"])
    def test_txt_datetime_guard_field_without_formats_raises(self, field: str) -> None:
        """A started_at or ended_at rule with no datetime_formats raises ValueError naming it."""
        with pytest.raises(ValueError, match="datetime_formats"):
            TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](**{field: re.compile(r"X:(.+)")}))


class TestSplitTzFmts:
    """`bdf._datetime_fmt.split_tz_fmts` classifies formats by embedded offset directive."""

    @pytest.mark.parametrize("fmt", ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%:z", "%Y-%m-%d %H:%M:%S %Z"])
    def test_format_with_offset_directive_is_tz_aware(self, fmt: str) -> None:
        """A format carrying %z, %:z, or %Z lands in the tz-aware group."""
        tz_aware, naive = split_tz_fmts((fmt,))
        assert tz_aware == [fmt]
        assert naive == []

    def test_format_without_offset_directive_is_naive(self) -> None:
        """A format with no offset directive lands in the naive group."""
        tz_aware, naive = split_tz_fmts(("%Y-%m-%d %H:%M:%S",))
        assert tz_aware == []
        assert naive == ["%Y-%m-%d %H:%M:%S"]

    def test_each_group_preserves_input_order(self) -> None:
        """Formats within each group keep the order they were given in."""
        fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%:z")
        tz_aware, naive = split_tz_fmts(fmts)
        assert tz_aware == ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%:z"]
        assert naive == ["%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"]


class TestToEpochSeconds:
    """`bdf._datetime_fmt.to_epoch_seconds` parses text with the first matching format."""

    def test_naive_format_localises_to_tz_and_reports_naive_flag(self) -> None:
        """A naive format localises the parsed value to tz and reports the naive flag True."""
        epoch, naive = to_epoch_seconds("01.01.2024 10:00:00", ("%d.%m.%Y %H:%M:%S",), "Europe/Berlin")
        assert epoch == 1704099600
        assert naive is True

    def test_offset_bearing_text_keeps_its_own_offset(self) -> None:
        """Text carrying its own UTC offset ignores the tz argument and reports the naive flag False."""
        epoch, naive = to_epoch_seconds("2024-01-01T10:00:00+02:00", ("%Y-%m-%dT%H:%M:%S%z",), "UTC")
        assert epoch == 1704096000
        assert naive is False

    def test_tz_aware_candidate_wins_over_a_later_naive_one(self) -> None:
        """A tz-aware format earlier in the tuple is tried, and wins, ahead of a later naive one."""
        epoch, naive = to_epoch_seconds(
            "2024-01-01T10:00:00+02:00",
            ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"),
            "UTC",
        )
        assert epoch == 1704096000
        assert naive is False

    def test_text_no_candidate_parses_returns_none_and_false(self) -> None:
        """Text no candidate format parses returns (None, False)."""
        epoch, naive = to_epoch_seconds("not-a-date", ("%d.%m.%Y %H:%M:%S",), "UTC")
        assert epoch is None
        assert naive is False


class TestCoercePreambleDatetimes:
    """`bdf.metadata_parsers._coerce_preamble_datetimes` coerces matched datetime text."""

    def test_datetime_field_that_parses_is_replaced_by_epoch_seconds(self) -> None:
        """A datetime field whose text parses is replaced by integer epoch seconds."""
        result = _coerce_preamble_datetimes(
            {"started_at": "01.01.2024 10:00:00"}, ("%d.%m.%Y %H:%M:%S",), "Europe/Berlin"
        )
        assert result == {"started_at": 1704099600}

    def test_datetime_field_that_does_not_parse_is_dropped(self) -> None:
        """A datetime field whose text no format parses is dropped from the result."""
        result = _coerce_preamble_datetimes({"started_at": "not-a-date"}, ("%d.%m.%Y %H:%M:%S",), "UTC")
        assert result == {}

    def test_non_datetime_field_passes_through_untouched(self) -> None:
        """A non-datetime field is copied through unchanged, regardless of its text."""
        result = _coerce_preamble_datetimes({"instrument_name": "Maccor 4000"}, ("%d.%m.%Y %H:%M:%S",), "UTC")
        assert result == {"instrument_name": "Maccor 4000"}


class TestJsonSidecarParser:
    def test_json_matches_true_when_sidecar_exists(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text("{}")
        assert JsonSidecarParser().matches(data) is True

    def test_json_matches_false_when_no_sidecar(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        assert JsonSidecarParser().matches(data) is False

    def test_json_parse_resolves_synonyms(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"InstrumentName": "Maccor 4000"}))
        parser = JsonSidecarParser(
            key_synonyms=MetadataRules(instrument_name=("instrument_name", "InstrumentName", "device"))
        )
        assert _parsed_fields(parser.parse(data)) == {"instrument_name": "Maccor 4000"}

    def test_json_parse_returns_only_matched_fields(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"other": "x"}))
        parser = JsonSidecarParser(key_synonyms=MetadataRules(instrument_name=("instrument_name",)))
        assert _parsed_fields(parser.parse(data)) == {}

    def test_json_parse_no_sidecar_returns_empty(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        parser = JsonSidecarParser(key_synonyms=MetadataRules(instrument_name=("instrument_name",)))
        assert _parsed_fields(parser.parse(data)) == {}

    def test_json_parse_first_synonym_in_order_wins(self, tmp_path: Path) -> None:
        """match_one picks the first synonym present in tuple order, not file order."""
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(
            json.dumps({"InstrumentName": "tuple_second", "instrument_name": "tuple_first"})
        )
        parser = JsonSidecarParser(key_synonyms=MetadataRules(instrument_name=("instrument_name", "InstrumentName")))
        assert _parsed_fields(parser.parse(data)) == {"instrument_name": "tuple_first"}

    def test_json_parse_coerces_non_string_value(self, tmp_path: Path) -> None:
        """match_one coerces a non-string JSON value with str()."""
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"instrument_name": 4000}))
        parser = JsonSidecarParser(key_synonyms=MetadataRules(instrument_name=("instrument_name",)))
        assert _parsed_fields(parser.parse(data)) == {"instrument_name": "4000"}

    def test_json_is_hashable(self) -> None:
        parser = JsonSidecarParser(key_synonyms=MetadataRules(instrument_name=("instrument_name",)))
        assert parser in frozenset({parser})


class TestMixedParserTypes:
    """Mixed parser types coexist in a frozenset."""

    def test_parsers_share_a_frozenset(self) -> None:
        parsers = frozenset(
            {
                MetadataParser(),
                TxtPreambleParser(magic=("x",)),
                JsonSidecarParser(),
            }
        )
        assert len(parsers) == 3
