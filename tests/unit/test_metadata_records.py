"""Unit tests for the read-metadata handoff (``bdf.metadata.Metadata``).

Cover the six-entity-record shape of :class:`Metadata`, the schema-bounded
records each field auto-constructs, and lossless ``to_dict()`` and
``model_dump()`` round-tripping.
"""

from __future__ import annotations

import json
import pickle
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from bdf.battinfo.record import Record, SchemaError
from bdf.battinfo.schema import bundled_schema_dir, load_docs
from bdf.metadata import _ENTITY_DOCS, BdfReadInfo, Metadata

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


def test_every_entity_record_field_is_registered() -> None:
    """The entity registry and the declared record fields name the same entities,
    and every registered document is one the bundle manages."""
    record_fields = {name for name in Metadata.model_fields if name.startswith("battinfo_")}
    assert set(_ENTITY_DOCS) == record_fields
    assert set(_ENTITY_DOCS.values()) <= set(load_docs(bundled_schema_dir()))


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


# ---------------------------------------------------------------------------
# Pydantic behaviour of the entity record fields
# ---------------------------------------------------------------------------


_DOC = {
    "battinfo_test": {"test": {"started_at": 1704164645, "conditions": {"temperature_c": {"typical_value": 25.0}}}},
    "battinfo_cell": {"cell_instance": {"name": "Cell-01"}},
}


def test_a_json_round_trip_restores_the_same_metadata() -> None:
    """`model_validate_json` reads back what `model_dump_json` wrote."""
    meta = Metadata.model_validate(_DOC)
    restored = Metadata.model_validate_json(meta.model_dump_json())
    assert restored == meta
    assert isinstance(restored.battinfo_test, Record)


def test_every_refused_value_is_its_own_error_located_at_the_value() -> None:
    """Each value the schema refuses is a separate error, whose location
    runs from the record field to the value, in python and JSON mode alike."""
    bad = {
        "battinfo_test": {"test": {"started_at": "x", "conditions": {"t": {"typical_value": "y"}}}},
        "battinfo_cell": {"cell_instance": {"battery_status": "melted"}},
    }
    with pytest.raises(ValidationError) as from_python:
        Metadata.model_validate(bad)
    with pytest.raises(ValidationError) as from_json:
        Metadata.model_validate_json(json.dumps(bad))
    for exc_info in (from_python, from_json):
        located = {error["loc"]: error["type"] for error in exc_info.value.errors()}
        assert located == {
            ("battinfo_test", "test", "started_at"): "battinfo_schema",
            ("battinfo_test", "test", "conditions", "t", "typical_value"): "battinfo_schema",
            ("battinfo_cell", "cell_instance", "battery_status"): "battinfo_schema",
        }


def test_construction_coerces_each_leaf_as_lax_mode_does() -> None:
    """A mapping is coerced before it validates: an integral string to an
    integer, and a number to a float."""
    meta = Metadata.model_validate(
        {"battinfo_test": {"test": {"started_at": "1704164645", "conditions": {"t": {"typical_value": 25}}}}}
    )
    assert meta.battinfo_test.test.started_at == 1704164645
    assert meta.battinfo_test.test.conditions.t.typical_value == 25.0
    assert isinstance(meta.battinfo_test.test.conditions.t.typical_value, float)


def test_assigning_a_whole_record_coerces_and_validates_it() -> None:
    """A whole-record assignment builds the record as construction does."""
    meta = Metadata()
    meta.battinfo_test = {"test": {"started_at": "1704164645"}}  # type: ignore[assignment]
    assert isinstance(meta.battinfo_test, Record)
    assert meta.battinfo_test.test.started_at == 1704164645
    with pytest.raises(ValidationError):
        meta.battinfo_test = {"test": {"started_at": "x"}}  # type: ignore[assignment]


def test_a_record_passed_in_is_validated() -> None:
    """A record built over unchecked data is refused as a mapping would be."""
    from bdf.metadata import _root_record

    unchecked = _root_record("cell-instance.schema.json", {"cell_instance": {"battery_status": "melted"}})
    with pytest.raises(ValidationError) as exc_info:
        Metadata(battinfo_cell=unchecked)
    assert exc_info.value.errors()[0]["loc"] == ("battinfo_cell", "cell_instance", "battery_status")


def test_a_keyed_entry_reads_by_subscript() -> None:
    """A map keyed by pattern reads by subscript, as a plain mapping did."""
    meta = Metadata.model_validate(_DOC)
    assert meta.battinfo_test.test.conditions["temperature_c"].typical_value == 25.0


def test_metadata_pickles() -> None:
    """A metadata instance survives a pickle round trip."""
    meta = Metadata.model_validate(_DOC)
    assert pickle.loads(pickle.dumps(meta)) == meta


def test_the_json_schema_describes_each_record_by_the_loaded_schema(tmp_path: Path) -> None:
    """Each record field's JSON Schema is the loaded BattINFO root node, and
    a substituted set changes it."""
    from bdf.battinfo.schema import BATTINFO_SCHEMA, bundled_schema_dir, load_bundled

    described = Metadata.model_json_schema()["properties"]["battinfo_test"]
    assert "test" in described["properties"]
    json.dumps(Metadata.model_json_schema())

    substituted = tmp_path / "schemas"
    shutil.copytree(bundled_schema_dir(), substituted)
    test_doc = substituted / "test.schema.json"
    doc = json.loads(test_doc.read_text(encoding="utf-8"))
    doc["properties"]["rig_bay"] = {"type": "string"}
    test_doc.write_text(json.dumps(doc), encoding="utf-8")
    try:
        BATTINFO_SCHEMA.load_dir(substituted)
        assert "rig_bay" in Metadata.model_json_schema()["properties"]["battinfo_test"]["properties"]
    finally:
        BATTINFO_SCHEMA._adopt(load_bundled())
    assert "rig_bay" not in Metadata.model_json_schema()["properties"]["battinfo_test"]["properties"]
