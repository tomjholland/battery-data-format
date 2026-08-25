"""Tests for reading, writing, and serialising a schema-bounded record."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pydantic
import pytest

from bdf.battinfo.record import Record
from bdf.battinfo.schema import BattinfoSchema, SchemaError

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.update_battinfo import load_managed  # noqa: E402

_PINNED_REF = "5ff1a6f2590795765978bd8088e2e6a8c5c146f3"
_PINNED_SCHEMA_VERSION = "0.2.0"


def _load_managed() -> dict[str, dict[str, Any]]:
    """Load the bundled managed schema documents."""
    return load_managed()


def _cell_instance_record() -> tuple[BattinfoSchema, Record]:
    """Build a fresh, empty `Record` bounded by the cell-instance root node."""
    docs = _load_managed()
    schema = BattinfoSchema(docs=docs, version=_PINNED_SCHEMA_VERSION, ref=_PINNED_REF)
    doc = docs["cell-instance.schema.json"]
    resolved, resolved_id = schema.resolve(doc, doc["$id"])
    return schema, Record(schema, resolved, resolved_id, data={}, path="")


def _cell_instance_record_over(data: dict[str, Any]) -> Record:
    """Build a `Record` bounded by the cell-instance root node, over `data`."""
    docs = _load_managed()
    schema = BattinfoSchema(docs=docs, version=_PINNED_SCHEMA_VERSION, ref=_PINNED_REF)
    doc = docs["cell-instance.schema.json"]
    resolved, resolved_id = schema.resolve(doc, doc["$id"])
    return Record(schema, resolved, resolved_id, data=data, path="")


def _quantity_record() -> Record:
    """Build a fresh, empty `Record` bounded by the `Quantity` root node."""
    docs = _load_managed()
    schema = BattinfoSchema(docs=docs, version=_PINNED_SCHEMA_VERSION, ref=_PINNED_REF)
    doc = docs["modules/common/quantity.schema.json"]
    resolved, resolved_id = schema.resolve(doc, doc["$id"])
    return Record(schema, resolved, resolved_id, data={}, path="")


def _conditions_record() -> Record:
    """Build a fresh, empty `Record` bounded by `Quantity.conditions`."""
    docs = _load_managed()
    schema = BattinfoSchema(docs=docs, version=_PINNED_SCHEMA_VERSION, ref=_PINNED_REF)
    doc = docs["modules/common/quantity.schema.json"]
    node = doc["properties"]["conditions"]
    resolved, resolved_id = schema.resolve(node, doc["$id"])
    return Record(schema, resolved, resolved_id, data={}, path="conditions")


class TestAssignmentAndRead:
    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_nested_assignment_needs_no_guard(self) -> None:
        """battinfo-records: A nested assignment needs no guard."""
        _, record = _cell_instance_record()
        record.cell_instance.name = "cell-1"
        assert record.cell_instance.name == "cell-1"

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_an_unset_leaf_reads_as_none(self) -> None:
        """battinfo-records: An unset leaf reads as None."""
        _, record = _cell_instance_record()
        assert record.cell_instance.name is None

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
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
    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_matching_entry_name_is_accepted(self) -> None:
        """battinfo-records: A matching entry name is accepted."""
        record = _conditions_record()
        record.max_current.value = 5.0
        assert record.max_current.value == 5.0

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_name_outside_the_pattern_is_refused(self) -> None:
        """battinfo-records: A name outside the pattern is refused."""
        record = _conditions_record()
        with pytest.raises(SchemaError) as exc_info:
            record.MaxCurrent = {}
        message = str(exc_info.value)
        assert "MaxCurrent" in message
        assert _PINNED_SCHEMA_VERSION in message


class TestRefusals:
    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_value_outside_an_enumeration_is_refused(self) -> None:
        """battinfo-records: A value outside an enumeration is refused."""
        _, record = _cell_instance_record()
        with pytest.raises(SchemaError) as exc_info:
            record.cell_instance.battery_status = "not-a-status"
        message = str(exc_info.value)
        assert "battery_status" in message
        for accepted in ("original", "repurposed", "re-used", "remanufactured", "waste"):
            assert accepted in message

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_value_against_a_pattern_is_refused(self) -> None:
        """battinfo-records: A value against a pattern is refused."""
        _, record = _cell_instance_record()
        with pytest.raises(SchemaError) as exc_info:
            record.cell_instance.short_id = "!!!"
        message = str(exc_info.value)
        assert "short_id" in message
        assert "0-9a-hjkmnp-tv-z" in message

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_value_outside_a_numeric_bound_is_refused(self) -> None:
        """A value outside a declared numeric bound is refused."""
        record = _quantity_record()
        with pytest.raises(SchemaError) as exc_info:
            record.sample_count = 0
        assert "sample_count" in str(exc_info.value)

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_value_of_the_wrong_type_is_refused(self) -> None:
        """battinfo-records: A value of the wrong type is refused."""
        _, record = _cell_instance_record()
        with pytest.raises(SchemaError) as exc_info:
            record.cell_instance.name = 123
        assert "name" in str(exc_info.value)

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_an_undeclared_field_names_the_schema_version(self) -> None:
        """battinfo-records: An undeclared field names the schema version."""
        _, record = _cell_instance_record()
        with pytest.raises(AttributeError) as exc_info:
            record.cell_instance.totally_unknown_field = 1
        message = str(exc_info.value)
        assert "totally_unknown_field" in message
        assert "cell_instance" in message
        assert _PINNED_SCHEMA_VERSION in message


class TestSerialisation:
    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_an_unset_field_is_absent(self) -> None:
        """battinfo-records: An unset field is absent."""
        _, record = _cell_instance_record()
        record.cell_instance.name = "cell-1"
        assert record.to_dict() == {"cell_instance": {"name": "cell-1"}}

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_an_empty_record_serialises_to_nothing(self) -> None:
        """battinfo-records: An empty record serialises to nothing."""
        _, record = _cell_instance_record()
        assert record.to_dict() == {}

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
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
    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_a_whole_record_validation_lists_every_error(self) -> None:
        """battinfo-records: A whole-record validation lists every error."""
        record = _cell_instance_record_over({"cell_instance": {"battery_status": "not-a-status", "short_id": "!!!"}})
        errors = record.validate()
        assert len(errors) == 2
        joined = " ".join(errors)
        assert "battery_status" in joined
        assert "short_id" in joined

    @pytest.mark.xfail(strict=True, reason="bdf.battinfo.record not implemented")
    def test_an_incomplete_record_reports_no_error(self) -> None:
        """battinfo-records: An incomplete record reports no error."""
        record = _cell_instance_record_over({"cell_instance": {"name": "cell-1"}})
        assert record.validate() == []
