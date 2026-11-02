"""Tests for BattINFO entity data held as plain dicts: path writes, reads, and whole-document checks."""

from __future__ import annotations

import pydantic
import pytest

from bdf import Metadata
from bdf.battinfo import BATTINFO_SCHEMA, SchemaError

_TABLE = {"type": "Table", "url": "a.csv", "table_schema": {"columns": [{"name": "t"}]}}


def test_set_creates_the_mappings_above_a_value() -> None:
    """A write on a fresh instance needs no None guard: the mappings above it are made."""
    meta = Metadata()
    meta.set("battinfo_test.test.name", "cell-1 formation")
    assert meta.battinfo_test == {"test": {"name": "cell-1 formation"}}
    assert meta.get("battinfo_test.test.name") == "cell-1 formation"


def test_get_returns_the_default_where_nothing_is_set() -> None:
    """A path that holds nothing reads as the default, at any depth."""
    meta = Metadata()
    assert meta.get("battinfo_cell.cell_instance.name") is None
    assert meta.get("battinfo_cell.cell_instance.name", "unnamed") == "unnamed"


def test_either_choice_of_an_any_of_is_accepted() -> None:
    """jsonschema decides the anyOf, so a TableGroup is accepted as well as a Table."""
    meta = Metadata()
    meta.set("battinfo_dataset.dataset.main_entity", _TABLE)
    meta.set("battinfo_dataset.dataset.main_entity", {"type": "TableGroup", "table": [_TABLE]})
    assert meta.get("battinfo_dataset.dataset.main_entity.type") == "TableGroup"


def test_a_value_neither_choice_accepts_is_refused_at_the_choice() -> None:
    """A value no alternative accepts is refused, and the message names the field holding the choice."""
    meta = Metadata()
    with pytest.raises(SchemaError, match=r"battinfo_dataset\.dataset\.main_entity"):
        meta.set("battinfo_dataset.dataset.main_entity", {"type": "Neither"})


def test_an_additional_properties_map_takes_an_entry_and_coerces_it() -> None:
    """An entry of a map declared by additionalProperties is checked, and its string number coerced."""
    meta = Metadata()
    meta.set("battinfo_test_protocol.conditions.ambient_temperature", {"value": "25", "unit": "degC"})
    assert meta.get("battinfo_test_protocol.conditions.ambient_temperature") == {"value": 25.0, "unit": "degC"}


def test_a_staged_string_is_coerced_on_validation() -> None:
    """A vendor header's string reaches the field as the number the schema declares."""
    meta = Metadata.model_validate(
        {"battinfo_test_protocol": {"conditions": {"ambient_temperature": {"value": "25", "unit": "degC"}}}}
    )
    assert meta.get("battinfo_test_protocol.conditions.ambient_temperature.value") == 25.0


def test_a_misspelled_field_is_refused() -> None:
    """additionalProperties: false refuses a misspelled field, and nothing is stored."""
    meta = Metadata()
    with pytest.raises(SchemaError, match="nmae"):
        meta.set("battinfo_test.test.nmae", "x")
    assert meta.battinfo_test == {}


def test_a_refused_type_names_the_field() -> None:
    """A value of the wrong type is refused, naming its path and the release."""
    meta = Metadata()
    with pytest.raises(SchemaError, match=rf"{BATTINFO_SCHEMA.version} refuses battinfo_test\.test\.name"):
        meta.set("battinfo_test.test.name", 3)


def test_none_unsets_a_value() -> None:
    """Writing None removes the value, and the mappings left empty with it."""
    meta = Metadata()
    meta.set("battinfo_test.test.name", "cell-1")
    meta.set("battinfo_test.test.name", None)
    assert meta.battinfo_test == {}


def test_set_refuses_a_path_that_names_no_entity() -> None:
    """The first segment must be an entity field."""
    with pytest.raises(AttributeError, match="battinfo_nope"):
        Metadata().set("battinfo_nope.x", 1)


def test_assigning_a_whole_entity_is_checked() -> None:
    """Assigning an entity's dict validates it, located at the refused value."""
    meta = Metadata()
    with pytest.raises(pydantic.ValidationError, match=r"battinfo_test\.test\.name"):
        meta.battinfo_test = {"test": {"name": 3}}


def test_check_is_empty_for_data_the_schema_accepts() -> None:
    """check() lists nothing where every entity follows the schema."""
    meta = Metadata()
    meta.set("battinfo_test.test.name", "cell-1")
    assert meta.check() == []
