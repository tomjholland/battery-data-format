"""Tests for reading, writing, and serialising a schema-bounded record."""

from __future__ import annotations

from typing import Any

import pydantic
import pytest

from bdf.battinfo.record import Record
from bdf.battinfo.schema import BattinfoSchema, SchemaError, bundled_schema_dir, load_docs

_PINNED_VERSION = "0b4dfd3fe70ccf2e6cc68e23aebb3fc8d9120c88"


def _bundled_docs() -> dict[str, dict[str, Any]]:
    """Load the bundled schema documents."""
    return load_docs(bundled_schema_dir())


def _cell_instance_record() -> tuple[BattinfoSchema, Record]:
    """Build a fresh, empty `Record` bounded by the cell-instance root node."""
    docs = _bundled_docs()
    schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
    doc = docs["cell-instance.schema.json"]
    resolved, resolved_id = schema.resolve(doc, doc["$id"])
    return schema, Record(schema, resolved, resolved_id, data={}, path="")


def _cell_instance_record_over(data: dict[str, Any]) -> Record:
    """Build a `Record` bounded by the cell-instance root node, over `data`."""
    docs = _bundled_docs()
    schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
    doc = docs["cell-instance.schema.json"]
    resolved, resolved_id = schema.resolve(doc, doc["$id"])
    return Record(schema, resolved, resolved_id, data=data, path="")


def _quantity_record() -> Record:
    """Build a fresh, empty `Record` bounded by the `Quantity` root node."""
    docs = _bundled_docs()
    schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
    doc = docs["modules/common/quantity.schema.json"]
    resolved, resolved_id = schema.resolve(doc, doc["$id"])
    return Record(schema, resolved, resolved_id, data={}, path="")


def _conditions_record() -> Record:
    """Build a fresh, empty `Record` bounded by `Quantity.conditions`."""
    docs = _bundled_docs()
    schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
    doc = docs["modules/common/quantity.schema.json"]
    node = doc["properties"]["conditions"]
    resolved, resolved_id = schema.resolve(node, doc["$id"])
    return Record(schema, resolved, resolved_id, data={}, path="conditions")


class TestAssignmentAndRead:
    def test_a_nested_assignment_needs_no_guard(self) -> None:
        """battinfo-records: A nested assignment needs no guard."""
        _, record = _cell_instance_record()
        record.cell_instance.name = "cell-1"
        assert record.cell_instance.name == "cell-1"

    def test_an_unset_leaf_reads_as_none(self) -> None:
        """battinfo-records: An unset leaf reads as None."""
        _, record = _cell_instance_record()
        assert record.cell_instance.name is None

    def test_a_choice_typed_value_reads_directly(self) -> None:
        """battinfo-records: A choice-typed value reads directly."""
        record = _quantity_record()
        record.value = 5.0
        assert record.value == 5.0
        assert not isinstance(record.value, Record)
        record.value = "five"
        assert record.value == "five"
        assert not isinstance(record.value, Record)


class TestKeyPattern:
    def test_a_matching_entry_name_is_accepted(self) -> None:
        """battinfo-records: A matching entry name is accepted."""
        record = _conditions_record()
        record.max_current.value = 5.0
        assert record.max_current.value == 5.0

    def test_a_name_outside_the_pattern_is_refused(self) -> None:
        """battinfo-records: A name outside the pattern is refused."""
        record = _conditions_record()
        with pytest.raises(SchemaError) as exc_info:
            record.MaxCurrent = {}
        message = str(exc_info.value)
        assert "MaxCurrent" in message
        assert _PINNED_VERSION in message


class TestRefusals:
    def test_a_value_outside_an_enumeration_is_refused(self) -> None:
        """battinfo-records: A value outside an enumeration is refused."""
        _, record = _cell_instance_record()
        with pytest.raises(SchemaError) as exc_info:
            record.cell_instance.battery_status = "not-a-status"
        message = str(exc_info.value)
        assert "battery_status" in message
        for accepted in ("original", "repurposed", "re-used", "remanufactured", "waste"):
            assert accepted in message

    def test_a_value_against_a_pattern_is_refused(self) -> None:
        """battinfo-records: A value against a pattern is refused."""
        _, record = _cell_instance_record()
        with pytest.raises(SchemaError) as exc_info:
            record.cell_instance.short_id = "!!!"
        message = str(exc_info.value)
        assert "short_id" in message
        assert "0-9a-hjkmnp-tv-z" in message

    def test_a_value_outside_a_numeric_bound_is_refused(self) -> None:
        """A value outside a declared numeric bound is refused."""
        record = _quantity_record()
        with pytest.raises(SchemaError) as exc_info:
            record.sample_count = 0
        assert "sample_count" in str(exc_info.value)

    def test_a_value_of_the_wrong_type_is_refused(self) -> None:
        """battinfo-records: A value of the wrong type is refused."""
        _, record = _cell_instance_record()
        with pytest.raises(SchemaError) as exc_info:
            record.cell_instance.name = 123
        assert "name" in str(exc_info.value)

    def test_an_undeclared_field_names_the_schema_version(self) -> None:
        """battinfo-records: An undeclared field names the schema version."""
        _, record = _cell_instance_record()
        with pytest.raises(AttributeError) as exc_info:
            record.cell_instance.totally_unknown_field = 1
        message = str(exc_info.value)
        assert "totally_unknown_field" in message
        assert "cell_instance" in message
        assert _PINNED_VERSION in message


class TestSerialisation:
    def test_an_unset_field_is_absent(self) -> None:
        """battinfo-records: An unset field is absent."""
        _, record = _cell_instance_record()
        record.cell_instance.name = "cell-1"
        assert record.to_dict() == {"cell_instance": {"name": "cell-1"}}

    def test_an_empty_record_serialises_to_nothing(self) -> None:
        """battinfo-records: An empty record serialises to nothing."""
        _, record = _cell_instance_record()
        assert record.to_dict() == {}

    def test_pydantic_dump_matches_to_dict(self) -> None:
        """A record inside a pydantic model dumps to what `to_dict` returns."""
        _, record = _cell_instance_record()
        record.cell_instance.name = "cell-1"

        class _Wrapper(pydantic.BaseModel):
            record: Record | None = None

        wrapper = _Wrapper(record=record)
        dumped = wrapper.model_dump(mode="json", exclude_defaults=True)
        assert dumped["record"] == record.to_dict()


class TestValidation:
    def test_a_whole_record_validation_lists_every_error(self) -> None:
        """battinfo-records: A whole-record validation lists every error."""
        record = _cell_instance_record_over({"cell_instance": {"battery_status": "not-a-status", "short_id": "!!!"}})
        errors = record.validate()
        assert len(errors) == 2
        joined = " ".join(errors)
        assert "battery_status" in joined
        assert "short_id" in joined

    def test_an_incomplete_record_reports_no_error(self) -> None:
        """battinfo-records: An incomplete record reports no error."""
        record = _cell_instance_record_over({"cell_instance": {"name": "cell-1"}})
        assert record.validate() == []


class TestEquality:
    def test_two_records_over_equal_data_are_equal(self) -> None:
        """battinfo-records: Two records over equal data are equal."""
        first = _cell_instance_record_over({"cell_instance": {"name": "cell-1"}})
        second = _cell_instance_record_over({"cell_instance": {"name": "cell-1"}})
        assert first == second

    def test_a_record_equals_no_mapping(self) -> None:
        """battinfo-records: A record equals no other type."""
        record = _cell_instance_record_over({"cell_instance": {"name": "cell-1"}})
        assert record != {"cell_instance": {"name": "cell-1"}}


class TestCoercion:
    def test_a_numeric_string_is_coerced_to_a_number(self) -> None:
        """A leaf declared ``number`` stores a numeric string as a float, the
        way pydantic's lax mode converts it."""
        record = _quantity_record()
        record.typical_value = "25"
        assert record.typical_value == 25.0
        assert isinstance(record.typical_value, float)

    def test_an_integer_is_stored_as_a_number(self) -> None:
        """An integer assigned to a ``number`` leaf is stored as a float."""
        record = _quantity_record()
        record.typical_value = 25
        assert isinstance(record.typical_value, float)

    def test_an_integral_string_is_coerced_to_an_integer(self) -> None:
        """A leaf declared ``integer`` stores an integral string as an int."""
        record = _quantity_record()
        record.sample_count = "3"
        assert record.sample_count == 3

    def test_a_value_that_does_not_coerce_is_refused_by_the_schema(self) -> None:
        """A value lax mode cannot convert is left alone, so the schema
        refuses it in its own terms."""
        record = _quantity_record()
        with pytest.raises(SchemaError, match="typical_value"):
            record.typical_value = "abc"
        with pytest.raises(SchemaError, match="sample_count"):
            record.sample_count = 1.5

    def test_a_boolean_is_never_coerced_to_a_number(self) -> None:
        """``True`` is not a number to the schema, and is not turned into one."""
        record = _quantity_record()
        with pytest.raises(SchemaError, match="typical_value"):
            record.typical_value = True

    def test_a_string_leaf_is_not_coerced(self) -> None:
        """A ``string`` leaf takes no conversion: a number is refused."""
        record = _quantity_record()
        with pytest.raises(SchemaError, match="unit_text"):
            record.unit_text = 25

    def test_a_nested_mapping_is_coerced_at_every_depth(self) -> None:
        """Assigning a mapping coerces each leaf it holds, through declared
        fields and key patterns alike."""
        record = _quantity_record()
        record.conditions = {"temperature_c": {"typical_value": "25", "sample_count": "2"}}
        assert record.conditions.temperature_c.typical_value == 25.0
        assert record.conditions.temperature_c.sample_count == 2


class TestSubscript:
    def test_a_keyed_entry_reads_by_subscript(self) -> None:
        """A map keyed by pattern reads as ``record[name]``, as it reads by
        attribute."""
        record = _conditions_record()
        record.max_current.typical_value = 5.0
        assert record["max_current"].typical_value == 5.0
        assert record["max_current"] == record.max_current

    def test_a_keyed_entry_writes_by_subscript(self) -> None:
        """A subscript write coerces and validates as an attribute write does."""
        record = _conditions_record()
        record["max_current"] = {"typical_value": "5"}
        assert record.max_current.typical_value == 5.0
        with pytest.raises(SchemaError, match="MaxCurrent"):
            record["MaxCurrent"] = {}

    def test_an_undeclared_field_raises_key_error(self) -> None:
        """A subscript on a node with no such field and no key pattern raises
        ``KeyError``."""
        record = _quantity_record()
        with pytest.raises(KeyError):
            record["no_such_field"]
        with pytest.raises(KeyError):
            record["no_such_field"] = 1

    def test_membership_reports_a_set_entry(self) -> None:
        """``name in record`` is true for an entry holding a value alone."""
        record = _conditions_record()
        assert "max_current" not in record
        _ = record.max_current
        assert "max_current" not in record
        record.max_current.typical_value = 5.0
        assert "max_current" in record
