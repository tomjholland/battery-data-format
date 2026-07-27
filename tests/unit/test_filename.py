"""Tests for the canonical data filename convention (parse/format)."""

from __future__ import annotations

import pytest

from bdf.filename import Parts, format, parse


def test_parse_canonical_filename() -> None:
    """The reference datastore's three-segment form parses into its parts."""
    parts = parse("Microsoft__manufacturer1-endt44198-2024-A0006__202603_5xC5-25degC.bdf.csv")
    assert parts == Parts(
        institution="Microsoft",
        cell="manufacturer1-endt44198-2024-A0006",
        date="202603",
        test="5xC5-25degC",
        ext="bdf.csv",
    )


def test_ambient_stays_inside_the_test_name() -> None:
    """Ambient conditions fold into the test name, not a separate part."""
    parts = parse("Inst__Cell-01__202603_5xC5-25degC.bdf.csv")
    assert parts.test == "5xC5-25degC"


def test_format_is_the_inverse_of_parse() -> None:
    """Formatting parts and re-parsing recovers the originals."""
    parts = Parts(institution="Inst", cell="Cell-01", date="20260301", test="C-1-25degC", ext="bdf.csv")
    assert parse(format(parts)) == parts


def test_format_emits_the_canonical_filename() -> None:
    """format() joins parts with the documented separators."""
    parts = Parts(institution="Inst", cell="Cell-01", date="20260301", test="C-1", ext="bdf.csv")
    assert format(parts) == "Inst__Cell-01__20260301_C-1.bdf.csv"


def test_readme_four_segment_variant_is_tolerated_on_read() -> None:
    """A separate-date, numeric-suffix variant parses, though it is not canonical."""
    parts = parse("Inst__Cell-01__20260301__001.bdf.csv")
    assert (parts.institution, parts.cell, parts.date, parts.test) == ("Inst", "Cell-01", "20260301", "001")


def test_parse_rejects_an_unrecognised_shape() -> None:
    """Neither three nor four ``__`` segments is a hard parse error."""
    with pytest.raises(ValueError, match="cannot parse filename"):
        parse("just-one-segment.bdf.csv")


def test_parse_requires_an_extension() -> None:
    """A filename with no extension is rejected rather than silently mis-split."""
    with pytest.raises(ValueError, match="no extension"):
        parse("Inst__Cell-01__202603_C-1")
