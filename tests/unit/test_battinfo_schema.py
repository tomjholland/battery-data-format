"""Tests for loading, resolving, and substituting a BattINFO schema set."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from bdf.battinfo import document
from bdf.battinfo.schema import BattinfoSchema, SchemaError, bundled_schema_dir, load_docs

from ._battinfo import write_snapshot

# The upstream release `src/bdf/data/battinfo/VERSION` names.
_PINNED_VERSION = "0.8.0rc2"


def _bundled_docs() -> dict[str, dict[str, Any]]:
    """Load the bundled schema documents, unmodified."""
    return load_docs(bundled_schema_dir())


def _substituted_docs(tmp_path: Path, edit: Any) -> dict[str, dict[str, Any]]:
    """Copy the bundled schema documents into a temporary directory and edit one.

    Args:
        tmp_path: A temporary directory to copy the bundled documents into.
        edit: A callable taking the dict of loaded documents and mutating one
            of them in place.

    Returns:
        The mapping of file name to parsed document, read back from the
        edited copy.
    """
    return load_docs(write_snapshot(tmp_path, edit=edit) / "schemas")


class TestBundledSet:
    def test_loading_needs_no_network(self) -> None:
        """battinfo-schema: Loading needs no network.

        With no network access, an install still builds a loaded set.
        """
        schema = BattinfoSchema.build()
        assert isinstance(schema, BattinfoSchema)

    def test_import_loads_bundled_set(self) -> None:
        """battinfo-schema: An import loads the bundled set.

        The loaded set reports the release the bundled ``VERSION`` names.
        """
        schema = BattinfoSchema.build()
        assert schema.version == _PINNED_VERSION


class TestSubstitution:
    def test_substituted_schema_widens_an_enumeration(self, tmp_path: Path) -> None:
        """battinfo-schema: A substituted schema widens an enumeration.

        A value the bundled set refuses is accepted under the substituted
        set, and the same value stays refused under the bundled set.
        """
        value = {"cell_instance": {"battery_status": "recertified"}}
        bundled = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
        assert document.refusals(bundled, "cell-instance.schema.json", value)

        def widen(docs: dict[str, dict[str, Any]]) -> None:
            enum = docs["cell-instance.schema.json"]["properties"]["cell_instance"]["properties"]["battery_status"][
                "enum"
            ]
            enum.append("recertified")

        substituted = BattinfoSchema(docs=_substituted_docs(tmp_path, widen))
        assert not document.refusals(substituted, "cell-instance.schema.json", value)

    def test_substituted_schema_declares_a_new_field(self, tmp_path: Path) -> None:
        """battinfo-schema: A substituted schema declares a new field.

        A field the bundled set refuses is accepted under the substituted
        set, and the same field stays refused under the bundled set.
        """
        value = {"cell_instance": {"custom_field": "x"}}
        bundled = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
        assert document.refusals(bundled, "cell-instance.schema.json", value)

        def add_field(docs: dict[str, dict[str, Any]]) -> None:
            properties = docs["cell-instance.schema.json"]["properties"]["cell_instance"]["properties"]
            properties["custom_field"] = {"type": "string"}

        substituted = BattinfoSchema(docs=_substituted_docs(tmp_path, add_field))
        assert not document.refusals(substituted, "cell-instance.schema.json", value)

    def test_substitution_runs_no_generated_code(self, tmp_path: Path) -> None:
        """battinfo-schema: Substitution runs no generated code.

        Building a record from a substituted schema set imports no module
        built from those documents.
        """

        def widen(docs: dict[str, dict[str, Any]]) -> None:
            enum = docs["cell-instance.schema.json"]["properties"]["cell_instance"]["properties"]["battery_status"][
                "enum"
            ]
            enum.append("recertified")

        substituted_docs = _substituted_docs(tmp_path, widen)
        for name in list(sys.modules):
            if name.startswith("bdf.battinfo.generated"):
                del sys.modules[name]
        BattinfoSchema(docs=substituted_docs)
        assert not any(name.startswith("bdf.battinfo.generated") for name in sys.modules)


class TestRequiredStripping:
    def test_incomplete_record_validates(self) -> None:
        """battinfo-schema: An incomplete record validates.

        An entity holding one field reports no error against the loaded
        (required-stripped) set, though the upstream document refuses it.
        """
        docs = _bundled_docs()
        schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
        value = {"cell_instance": {"name": "cell-1"}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, copy.deepcopy(docs["cell-instance.schema.json"]))
        assert not document.refusals(schema, "cell-instance.schema.json", value)

    def test_nested_required_marker_is_dropped_too(self) -> None:
        """battinfo-schema: A nested required marker is dropped too.

        An entity that omits a field required inside a nested object
        (`conformance` requires `status`) still validates.
        """
        schema = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
        value = {"cell_instance": {"conformance": {"note": "partial"}}}
        assert not document.refusals(schema, "cell-instance.schema.json", value)


class TestReferenceResolution:
    def test_cross_document_reference_resolves(self) -> None:
        """battinfo-schema: A cross-document reference resolves.

        `cell-instance.measured` follows the definition in
        `cell-canonical.schema.json`, with no network call: a field that
        definition declares is accepted, and an undeclared one refused.
        """
        schema = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
        doc = "cell-instance.schema.json"
        assert not document.refusals(schema, doc, {"measured": {"ac_internal_resistance": {}}})
        assert document.refusals(schema, doc, {"measured": {"not_a_measurement": {}}})

    def test_combined_node_checks_every_subschema(self) -> None:
        """battinfo-schema: A node combined under `allOf` checks the fields of each subschema."""
        docs: dict[str, dict[str, Any]] = {
            "combined.schema.json": {
                "$id": "https://example.test/combined.schema.json",
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "allOf": [
                    {"properties": {"left": {"type": "string"}}, "type": "object"},
                    {"properties": {"right": {"type": "number"}}, "type": "object"},
                ],
            }
        }
        schema = BattinfoSchema(docs=docs)
        assert not document.refusals(schema, "combined.schema.json", {"left": "a", "right": 1})
        assert document.refusals(schema, "combined.schema.json", {"right": "not a number"})

    def test_self_referring_node_validates_a_finite_value(self) -> None:
        """battinfo-schema: `Quantity.conditions` refers to Quantity itself, and a nested value validates."""
        schema = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
        value = {"value": 1.0, "unit": "V", "conditions": {"temperature": {"value": 25.0, "unit": "degC"}}}
        assert not document.refusals(schema, "modules/common/quantity.schema.json", value)


class TestReferenceErrors:
    def test_a_reference_loop_raises(self) -> None:
        """A reference loop that consumes nothing raises `SchemaError` naming the document."""
        docs: dict[str, dict[str, Any]] = {
            "chain.schema.json": {"$id": "https://example.test/chain.schema.json", "$ref": "#"}
        }
        schema = BattinfoSchema(docs=docs)
        with pytest.raises(SchemaError, match="chain.schema.json"):
            document.refusals(schema, "chain.schema.json", {})

    def test_unknown_identifier_raises(self) -> None:
        """An unknown identifier raises `SchemaError` naming it."""
        docs: dict[str, dict[str, Any]] = {
            "broken.schema.json": {
                "$id": "https://example.test/broken.schema.json",
                "properties": {"x": {"$ref": "https://example.test/does-not-exist.schema.json#/$defs/Nothing"}},
            }
        }
        schema = BattinfoSchema(docs=docs)
        with pytest.raises(SchemaError, match="does-not-exist.schema.json"):
            document.refusals(schema, "broken.schema.json", {"x": 1})


class TestDocumentErrors:
    def test_document_with_no_id_raises(self) -> None:
        """A document with no `$id` raises `ValueError` naming the file."""
        docs = {"broken.schema.json": {"type": "object"}}
        with pytest.raises(ValueError, match="broken.schema.json"):
            BattinfoSchema(docs=docs)


def test_a_loaded_set_and_metadata_checked_by_it_pickle() -> None:
    """A loaded set, and metadata checked by it, survive a pickle round trip:
    the reference registry is rebuilt from the documents."""
    import pickle

    from bdf import Metadata

    schema = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
    restored = pickle.loads(pickle.dumps(schema))
    assert restored.docs == schema.docs
    assert restored.version == schema.version
    assert document.refusals(restored, "cell-instance.schema.json", {"cell_instance": {"battery_status": "melted"}})
    meta = Metadata()
    meta.set("battinfo_cell.cell_instance.name", "Cell-01")
    again = pickle.loads(pickle.dumps(meta))
    assert again == meta
    with pytest.raises(SchemaError):
        again.set("battinfo_cell.cell_instance.battery_status", "melted")


def test_a_validator_is_built_once_and_adopting_a_set_clears_it(tmp_path: Path) -> None:
    """A set caches each document's validator, and adopting another set drops
    the cache, so a check never uses the documents a set replaced."""

    def declare_rig_bay(docs: dict[str, dict[str, Any]]) -> None:
        docs["test.schema.json"]["properties"]["rig_bay"] = {"type": "string"}

    schema = BattinfoSchema.build()
    validator = schema.root_validator("test.schema.json")
    assert schema.root_validator("test.schema.json") is validator
    assert document.refusals(schema, "test.schema.json", {"rig_bay": "B3"})

    schema._adopt(BattinfoSchema(_substituted_docs(tmp_path, declare_rig_bay)))
    assert not document.refusals(schema, "test.schema.json", {"rig_bay": "B3"})
