"""Unit tests for the read-metadata handoff (``bdf.metadata.Metadata``).

Cover the six-entity-record shape of :class:`Metadata`, the schema-bounded
records each field auto-constructs, and lossless ``to_dict()`` and
``model_dump()`` round-tripping.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bdf.battinfo.record import Record, SchemaError
from bdf.metadata import BdfReadInfo, Metadata

VALID_CELL_IRI = "https://w3id.org/battinfo/cell/abcd-efgh-jkmn-pqrs"

# An epoch with no significance beyond being a value the record accepts.
_PLACEHOLDER_EPOCH = 1700000000


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_read_metadata_declares_the_six_entity_records_bdf_raw_and_extras_only() -> None:
    """Metadata's field set is the six BattINFO entity records, bdf, raw, and extras."""
    assert set(Metadata.model_fields) == {
        "battinfo_test",
        "battinfo_cell",
        "battinfo_channel",
        "battinfo_equipment",
        "battinfo_test_protocol",
        "battinfo_dataset",
        "bdf",
        "raw",
        "extras",
    }


def test_fresh_read_metadata_auto_constructs_every_entity_record() -> None:
    """A freshly constructed Metadata carries a default record for each entity."""
    meta = Metadata()

    assert isinstance(meta.battinfo_test, Record)
    assert isinstance(meta.battinfo_cell, Record)
    assert isinstance(meta.battinfo_channel, Record)
    assert isinstance(meta.battinfo_equipment, Record)
    assert isinstance(meta.battinfo_test_protocol, Record)
    assert isinstance(meta.battinfo_dataset, Record)
    assert isinstance(meta.bdf, BdfReadInfo)
    assert meta.raw is None
    assert meta.extras is None


def test_nested_assignment_on_a_fresh_read_metadata_reaches_the_leaf() -> None:
    """meta.battinfo_test.test.started_at = ... works with no None guard on a fresh Metadata()."""
    meta = Metadata()
    meta.battinfo_test.test.started_at = _PLACEHOLDER_EPOCH

    assert meta.battinfo_test.test.started_at == _PLACEHOLDER_EPOCH
    assert meta.to_dict() == {"battinfo_test": {"test": {"started_at": _PLACEHOLDER_EPOCH}}}


# ---------------------------------------------------------------------------
# Strictness
# ---------------------------------------------------------------------------


def test_assignment_is_type_checked() -> None:
    """Assigning a non-integer to battinfo_test.test.started_at is refused."""
    meta = Metadata()

    with pytest.raises(SchemaError):
        meta.battinfo_test.test.started_at = "not-a-timestamp"


def test_bad_enum_value_is_refused() -> None:
    """A test.status value outside the declared literal set is refused."""
    meta = Metadata()

    with pytest.raises(SchemaError):
        meta.battinfo_test.test.status = "not-a-real-status"


def test_a_schema_constraint_enforces() -> None:
    """A cell_instance.short_id value that violates the schema's pattern is refused."""
    meta = Metadata()

    with pytest.raises(SchemaError):
        meta.battinfo_cell.cell_instance.short_id = "not valid!"


def test_a_non_conforming_id_fails() -> None:
    """A cell_id value of 'CELL001' does not conform to the IRI pattern, and is refused."""
    meta = Metadata()

    with pytest.raises(SchemaError):
        meta.battinfo_test.test.cell_id = "CELL001"


def test_an_undeclared_field_raises() -> None:
    """An undeclared key under a record's own fields raises, rather than being absorbed."""
    meta = Metadata()

    with pytest.raises(AttributeError):
        meta.battinfo_test.test.rig_bay = "B7"


def test_a_nested_undeclared_key_fails_construction() -> None:
    """A nested key no schema node declares refuses a Metadata built from a raw mapping."""
    with pytest.raises(ValidationError, match="extra_key"):
        Metadata.model_validate({"battinfo_test": {"test": {"conditions": {}, "extra_key": 1}}})


def test_a_valid_iri_passes_through() -> None:
    """A conforming cell_id IRI appears unchanged in the staged record."""
    meta = Metadata()
    meta.battinfo_test.test.cell_id = VALID_CELL_IRI

    assert meta.battinfo_test.test.cell_id == VALID_CELL_IRI


# ---------------------------------------------------------------------------
# A choice-typed leaf holds its value directly
# ---------------------------------------------------------------------------


def test_an_unset_commissioned_at_reads_as_none_and_to_dict_omits_it() -> None:
    """An unset choice-typed leaf reads as None, and to_dict() omits it."""
    meta = Metadata()

    assert meta.battinfo_equipment.equipment.commissioned_at is None
    assert "commissioned_at" not in meta.battinfo_equipment.to_dict().get("equipment", {})


def test_a_choice_typed_leaf_holds_its_value_directly() -> None:
    """A value assigned to a choice-typed leaf reads back as that value, with no wrapper."""
    meta = Metadata()
    meta.battinfo_equipment.equipment.commissioned_at = _PLACEHOLDER_EPOCH

    assert meta.battinfo_equipment.equipment.commissioned_at == _PLACEHOLDER_EPOCH
    assert not isinstance(meta.battinfo_equipment.equipment.commissioned_at, Record)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_to_dict_prunes_empty_sections() -> None:
    """A section left entirely unset vanishes from to_dict() rather than appearing as {}."""
    meta = Metadata()
    meta.battinfo_test.test.name = "Hydra.0b_C_GITTOCV_002b"
    meta.battinfo_test.test.started_at = _PLACEHOLDER_EPOCH

    assert meta.to_dict() == {
        "battinfo_test": {"test": {"name": "Hydra.0b_C_GITTOCV_002b", "started_at": _PLACEHOLDER_EPOCH}}
    }


def test_an_untouched_metadata_dumps_to_nothing() -> None:
    """A Metadata that carries nothing dumps to an empty mapping under exclude_defaults."""
    assert Metadata().model_dump(mode="json", exclude_defaults=True) == {}


def test_a_touched_entity_record_alone_reaches_the_dump() -> None:
    """Only the entity record a caller touched reaches an excluding dump."""
    meta = Metadata()
    meta.battinfo_test.test.instrument_name = "Arbin"

    dumped = meta.model_dump(mode="json", exclude_defaults=True)

    assert dumped == {"battinfo_test": {"test": {"instrument_name": "Arbin"}}}


def test_two_metadata_instances_with_the_same_content_are_equal() -> None:
    """Two separately built Metadata instances holding the same content compare equal."""
    first = Metadata()
    first.battinfo_test.test.instrument_name = "Arbin"
    second = Metadata()
    second.battinfo_test.test.instrument_name = "Arbin"

    assert first == second


def test_metadata_holding_different_content_is_unequal() -> None:
    """Metadata instances holding different content compare unequal."""
    first = Metadata()
    first.battinfo_test.test.instrument_name = "Arbin"

    assert first != Metadata()
