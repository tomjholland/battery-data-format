"""Tests for loading, resolving, and substituting a BattINFO schema set."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from bdf.battinfo.schema import BattinfoSchema, SchemaError, bundled_schema_dir, load_bundled, load_docs

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
    dest = tmp_path / "schemas"
    shutil.copytree(bundled_schema_dir(), dest)
    docs = load_docs(dest)
    edit(docs)
    for rel_name, doc in docs.items():
        (dest / rel_name).write_text(json.dumps(doc), encoding="utf-8")
    return load_docs(dest)


class TestBundledSet:
    def test_loading_needs_no_network(self) -> None:
        """battinfo-schema: Loading needs no network.

        With no network access, an install still builds a loaded set.
        """
        schema = load_bundled()
        assert isinstance(schema, BattinfoSchema)

    def test_import_loads_bundled_set(self) -> None:
        """battinfo-schema: An import loads the bundled set.

        The loaded set reports the release the bundled ``VERSION`` names.
        """
        schema = load_bundled()
        assert schema.version == _PINNED_VERSION


class TestSubstitution:
    def test_substituted_schema_widens_an_enumeration(self, tmp_path: Path) -> None:
        """battinfo-schema: A substituted schema widens an enumeration.

        A value the bundled set refuses is accepted under the substituted
        set, and the same value stays refused under the bundled set.
        """
        bundled_docs = _bundled_docs()
        bundled = BattinfoSchema(docs=bundled_docs, version=_PINNED_VERSION)
        bundled_doc = bundled_docs["cell-instance.schema.json"]
        node = bundled_doc["properties"]["cell_instance"]["properties"]["battery_status"]
        resolved, _ = bundled.resolve(node, bundled_doc["$id"])
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate("recertified", resolved)

        def widen(docs: dict[str, dict[str, Any]]) -> None:
            enum = docs["cell-instance.schema.json"]["properties"]["cell_instance"]["properties"]["battery_status"][
                "enum"
            ]
            enum.append("recertified")

        substituted_docs = _substituted_docs(tmp_path, widen)
        substituted = BattinfoSchema(docs=substituted_docs)
        substituted_doc = substituted_docs["cell-instance.schema.json"]
        sub_node = substituted_doc["properties"]["cell_instance"]["properties"]["battery_status"]
        sub_resolved, _ = substituted.resolve(sub_node, substituted_doc["$id"])
        jsonschema.validate("recertified", sub_resolved)

    def test_substituted_schema_declares_a_new_field(self, tmp_path: Path) -> None:
        """battinfo-schema: A substituted schema declares a new field.

        A field the bundled set refuses is accepted under the substituted
        set, and the same field stays refused under the bundled set.
        """
        bundled_docs = _bundled_docs()
        bundled = BattinfoSchema(docs=bundled_docs, version=_PINNED_VERSION)
        bundled_doc = bundled_docs["cell-instance.schema.json"]
        node = bundled_doc["properties"]["cell_instance"]
        resolved, _ = bundled.resolve(node, bundled_doc["$id"])
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"custom_field": "x"}, resolved)

        def add_field(docs: dict[str, dict[str, Any]]) -> None:
            properties = docs["cell-instance.schema.json"]["properties"]["cell_instance"]["properties"]
            properties["custom_field"] = {"type": "string"}

        substituted_docs = _substituted_docs(tmp_path, add_field)
        substituted = BattinfoSchema(docs=substituted_docs)
        substituted_doc = substituted_docs["cell-instance.schema.json"]
        sub_node = substituted_doc["properties"]["cell_instance"]
        sub_resolved, _ = substituted.resolve(sub_node, substituted_doc["$id"])
        jsonschema.validate({"custom_field": "x"}, sub_resolved)

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

        A record instance holding one field reports no validation error
        against the loaded (required-stripped) node.
        """
        docs = _bundled_docs()
        schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
        doc = docs["cell-instance.schema.json"]
        resolved, _ = schema.resolve(doc, doc["$id"])
        raw_doc = copy.deepcopy(doc)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"cell_instance": {"name": "cell-1"}}, raw_doc)
        jsonschema.validate({"cell_instance": {"name": "cell-1"}}, resolved)

    def test_nested_required_marker_is_dropped_too(self) -> None:
        """battinfo-schema: A nested required marker is dropped too.

        A record that omits a field required inside a nested object still
        validates against the loaded, stripped node.
        """
        docs = _bundled_docs()
        schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
        doc = docs["cell-instance.schema.json"]
        # `conformance` nests a `required: ["status"]` marker.
        node = doc["properties"]["cell_instance"]["properties"]["conformance"]
        resolved, _ = schema.resolve(node, doc["$id"])
        raw_node = copy.deepcopy(node)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"note": "partial"}, raw_node)
        jsonschema.validate({"note": "partial"}, resolved)


class TestReferenceResolution:
    def test_cross_document_reference_resolves(self) -> None:
        """battinfo-schema: A cross-document reference resolves.

        `cell-instance.measured` resolves to the definition in
        `cell-canonical.schema.json`, with no network call.
        """
        docs = _bundled_docs()
        schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
        cell_instance_doc = docs["cell-instance.schema.json"]
        node = cell_instance_doc["properties"]["measured"]
        resolved, resolved_id = schema.resolve(node, cell_instance_doc["$id"])
        assert resolved_id == docs["cell-canonical.schema.json"]["$id"]
        assert "ac_internal_resistance" in resolved.get("properties", {})

    def test_combined_node_exposes_every_field(self) -> None:
        """battinfo-schema: A combined node exposes every field.

        A node combined under `allOf` exposes the fields of each subschema.
        """
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
        doc = docs["combined.schema.json"]
        resolved, _ = schema.resolve(doc, doc["$id"])
        assert "left" in resolved["properties"]
        assert "right" in resolved["properties"]

    def test_self_referring_node_does_not_recurse_without_end(self) -> None:
        """battinfo-schema: A self-referring node does not recurse without end.

        `Quantity.conditions` resolves without unbounded recursion.
        """
        docs = _bundled_docs()
        schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
        quantity_doc = docs["modules/common/quantity.schema.json"]
        node = quantity_doc["properties"]["conditions"]
        resolved, _ = schema.resolve(node, quantity_doc["$id"])
        assert "patternProperties" in resolved


class TestReferenceErrors:
    def test_reference_chain_over_twenty_links_raises(self) -> None:
        """A reference chain over 20 links raises `SchemaError` naming the chain."""
        docs: dict[str, dict[str, Any]] = {"chain.schema.json": {"$id": "https://example.test/chain.schema.json"}}
        doc = docs["chain.schema.json"]
        node: dict[str, Any] = {"$ref": "#"}
        doc.update(node)
        schema = BattinfoSchema(docs=docs)
        with pytest.raises(SchemaError) as exc_info:
            schema.resolve(node, doc["$id"])
        assert "https://example.test/chain.schema.json" in str(exc_info.value)

    def test_unknown_identifier_raises(self) -> None:
        """An unknown identifier raises `SchemaError` naming it."""
        docs = _bundled_docs()
        schema = BattinfoSchema(docs=docs, version=_PINNED_VERSION)
        node = {"$ref": "https://example.test/does-not-exist.schema.json#/$defs/Nothing"}
        with pytest.raises(SchemaError) as exc_info:
            schema.resolve(node, docs["cell-instance.schema.json"]["$id"])
        assert "https://example.test/does-not-exist.schema.json#/$defs/Nothing" in str(exc_info.value)


class TestDocumentErrors:
    def test_document_with_no_id_raises(self) -> None:
        """A document with no `$id` raises `ValueError` naming the file."""
        docs = {"broken.schema.json": {"type": "object"}}
        with pytest.raises(ValueError, match="broken.schema.json"):
            BattinfoSchema(docs=docs)


def test_a_loaded_set_and_its_records_pickle() -> None:
    """A loaded set, and a record bounded by it, survive a pickle round trip:
    the reference registry is rebuilt from the documents."""
    import pickle

    from bdf.battinfo.record import Record

    schema = BattinfoSchema(docs=_bundled_docs(), version=_PINNED_VERSION)
    restored = pickle.loads(pickle.dumps(schema))
    assert restored.docs == schema.docs
    assert restored.version == schema.version
    doc = restored.docs["cell-instance.schema.json"]
    resolved, base_id = restored.resolve(doc, doc["$id"])
    record = Record(restored, resolved, base_id, {}, "")
    record.cell_instance.name = "Cell-01"
    again = pickle.loads(pickle.dumps(record))
    assert again == record
    with pytest.raises(SchemaError):
        again.cell_instance.battery_status = "melted"


def test_a_root_is_resolved_once_and_adopting_a_set_clears_it(tmp_path: Path) -> None:
    """A set caches each document's resolved root, and adopting another set drops
    the cache, so a root never comes from the documents a set replaced."""

    def declare_rig_bay(docs: dict[str, dict[str, Any]]) -> None:
        docs["test.schema.json"]["properties"]["rig_bay"] = {"type": "string"}

    schema = load_bundled()
    root = schema.root("test.schema.json")
    assert schema.root("test.schema.json") is root
    assert "rig_bay" not in root[0]["properties"]

    schema._adopt(BattinfoSchema(_substituted_docs(tmp_path, declare_rig_bay)))
    assert "rig_bay" in schema.root("test.schema.json")[0]["properties"]
