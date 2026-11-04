"""Unit tests for the read-metadata handoff (``bdf.metadata.Metadata``).

Cover the shape of :class:`Metadata`, entities as plain dicts that keep a
value as it was written, and lossless ``to_dict()`` round-tripping.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bdf.battinfo import ENTITY_DOCS
from bdf.metadata import BdfReadInfo, Metadata

# An epoch with no significance beyond being a value the record accepts.
_PLACEHOLDER_EPOCH = 1700000000

not_implemented = pytest.mark.xfail(strict=True, reason="BattINFO runtime schema not implemented")


def test_read_metadata_declares_the_six_entity_records_bdf_raw_and_extras_only() -> None:
    """Metadata's field set is the six BattINFO entity records, bdf, raw, and extras."""
    assert set(Metadata.model_fields) == {*ENTITY_DOCS, "bdf", "raw", "extras"}


@not_implemented
def test_fresh_read_metadata_holds_an_empty_dict_for_each_entity() -> None:
    """A freshly constructed Metadata carries an empty dict for each entity, and no entity is shared."""
    meta = Metadata()

    assert all(getattr(meta, entity) == {} for entity in ENTITY_DOCS)
    assert isinstance(meta.bdf, BdfReadInfo)
    assert meta.raw is None
    assert meta.extras is None
    meta.battinfo_test["test"] = {}
    assert Metadata().battinfo_test == {}


@not_implemented
def test_a_caller_writes_a_nested_value_with_dict_operations() -> None:
    """A nested value is written with dict operations, with no guard for a missing level."""
    meta = Metadata()

    meta.battinfo_test.setdefault("test", {})["name"] = "cell-1 formation"

    assert meta.battinfo_test == {"test": {"name": "cell-1 formation"}}


@not_implemented
def test_a_value_is_kept_as_it_was_written() -> None:
    """A string for a numeric field stays a string: the check refuses it, and nothing converts it."""
    meta = Metadata(battinfo_test={"test": {"started_at": "25"}})

    assert meta.battinfo_test["test"]["started_at"] == "25"
    assert any("battinfo_test.test.started_at" in line for line in meta.check())


def test_an_entity_must_still_be_a_dict() -> None:
    """An entity takes only a dict, at construction and at assignment."""
    # The entity's contents are checked by BattINFO, but its type is still checked by pydantic.
    with pytest.raises(ValidationError):
        Metadata(battinfo_test=["test"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Metadata().battinfo_cell = "cell"  # type: ignore[assignment]


def test_to_dict_leaves_out_empty_entities_and_round_trips() -> None:
    """to_dict() omits an entity nothing filled, keeps the others as written, and restores the same Metadata."""
    meta = Metadata(
        battinfo_test={"test": {"name": "Hydra.0b_C_GITTOCV_002b", "started_at": _PLACEHOLDER_EPOCH}},
        battinfo_channel={},
        extras={"rig_bay": "B7"},
    )

    assert meta.to_dict() == {
        "battinfo_test": {"test": {"name": "Hydra.0b_C_GITTOCV_002b", "started_at": _PLACEHOLDER_EPOCH}},
        "extras": {"rig_bay": "B7"},
    }
    assert Metadata.model_validate(meta.to_dict()) == meta


@not_implemented
def test_repr_and_str_show_only_filled_fields() -> None:
    """repr and str leave out defaults."""
    meta = Metadata(battinfo_test={"test": {"started_at": _PLACEHOLDER_EPOCH}})

    assert repr(meta) == f"Metadata(battinfo_test={{'test': {{'started_at': {_PLACEHOLDER_EPOCH}}}}})"
    assert str(meta) == f"battinfo_test={{'test': {{'started_at': {_PLACEHOLDER_EPOCH}}}}}"
    assert repr(Metadata()) == "Metadata()"
