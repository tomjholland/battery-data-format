"""Unit tests for bdf.metadata_parsers (MetadataRules and parser classes)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import bdf.metadata_parsers
from bdf.battinfo_records import TestSection
from bdf.metadata_parsers import (
    JsonSidecarParser,
    MetadataParser,
    MetadataRules,
    TxtPreambleParser,
)

START_TIME_RX = re.compile(r"~Start of Test:\s*(.+)")


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
        assert MetadataParser().parse(p) == {}

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
        p.write_text("header\n~Start of Test: 19.06.2023 17:56:53\nmore\n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](started_at=START_TIME_RX))
        assert parser.parse(p) == {"started_at": "19.06.2023 17:56:53"}

    def test_txt_parse_honours_encoding(self, tmp_path: Path) -> None:
        """parse() decodes the head with the configured encoding (latin-1)."""
        p = tmp_path / "latin1.txt"
        p.write_bytes("~Start of Test: caf\xe9\n".encode("latin-1"))
        parser = TxtPreambleParser(
            encoding="latin-1",
            regex_patterns=MetadataRules[re.Pattern[str]](started_at=START_TIME_RX),
        )
        assert parser.parse(p) == {"started_at": "caf\xe9"}

    def test_txt_parse_returns_only_matched_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("no relevant lines here\n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](started_at=START_TIME_RX))
        assert parser.parse(p) == {}

    def test_txt_parse_strips_captured_value(self, tmp_path: Path) -> None:
        """match_one strips surrounding whitespace from group(1)."""
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test:   19.06.2023   \n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](started_at=START_TIME_RX))
        assert parser.parse(p) == {"started_at": "19.06.2023"}

    def test_txt_parse_first_matching_line_wins(self, tmp_path: Path) -> None:
        """match_one returns the first matching line, ignoring later matches."""
        p = tmp_path / "f.txt"
        p.write_text("~Start of Test: first\n~Start of Test: second\n")
        parser = TxtPreambleParser(regex_patterns=MetadataRules[re.Pattern[str]](started_at=START_TIME_RX))
        assert parser.parse(p) == {"started_at": "first"}

    def test_txt_is_hashable(self) -> None:
        parser = TxtPreambleParser(
            magic=("x",),
            regex_patterns=MetadataRules[re.Pattern[str]](started_at=re.compile(r"a(.+)")),
        )
        assert parser in frozenset({parser})

    def test_txt_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            TxtPreambleParser(bogus="x")  # type: ignore[call-arg]


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
        (tmp_path / "cell.json").write_text(json.dumps({"StartTime": "2024-01-01"}))
        parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at", "StartTime", "test_start")))
        assert parser.parse(data) == {"started_at": "2024-01-01"}

    def test_json_parse_returns_only_matched_fields(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"other": "x"}))
        parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at",)))
        assert parser.parse(data) == {}

    def test_json_parse_no_sidecar_returns_empty(self, tmp_path: Path) -> None:
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at",)))
        assert parser.parse(data) == {}

    def test_json_parse_first_synonym_in_order_wins(self, tmp_path: Path) -> None:
        """match_one picks the first synonym present in tuple order, not file order."""
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"StartTime": "tuple_second", "started_at": "tuple_first"}))
        parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at", "StartTime")))
        assert parser.parse(data) == {"started_at": "tuple_first"}

    def test_json_parse_coerces_non_string_value(self, tmp_path: Path) -> None:
        """match_one coerces a non-string JSON value with str()."""
        data = tmp_path / "cell.csv"
        data.write_text("a,b\n1,2\n")
        (tmp_path / "cell.json").write_text(json.dumps({"started_at": 1700000000}))
        parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at",)))
        assert parser.parse(data) == {"started_at": "1700000000"}

    def test_json_is_hashable(self) -> None:
        parser = JsonSidecarParser(key_synonyms=MetadataRules(started_at=("started_at",)))
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
