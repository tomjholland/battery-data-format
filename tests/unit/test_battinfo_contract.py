"""Contract test: field names BDF writes are contract-tested against pinned
upstream BattINFO schemas (``tests/fixtures/battinfo/``).

Every field path declared on the hand-written ``bdf.battinfo_records`` models
must resolve to a property in the corresponding pinned upstream schema, so an
upstream rename that would silently break the handoff instead fails this test
with a one-line fix (rename the field on the hand-written model, refresh the
fixture per ``tests/fixtures/battinfo/README.md``).

Imports of ``bdf.battinfo_records`` are made inside each test body rather
than at module level.
"""

from __future__ import annotations

import json
import types
import typing
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent.parent / "fixtures" / "battinfo"


def _load_schema(name: str) -> dict[str, Any]:
    """Load a pinned upstream schema fixture by file name."""
    return json.loads((FIXTURES / name).read_text())


def _resolve(doc: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Follow a single local ``$ref`` (``#/$defs/Name``) within a schema doc.

    Args:
        doc: The full schema document, providing the ``$defs`` lookup table.
        node: The property (or ``items``) mapping to resolve; returned
            unchanged if it carries no ``$ref``.

    Returns:
        The referenced ``$defs`` entry, or ``node`` itself if unreferenced.
    """
    ref = node.get("$ref")
    if ref is None:
        return node
    prefix = "#/$defs/"
    assert ref.startswith(prefix), f"unsupported $ref target: {ref}"
    return doc["$defs"][ref[len(prefix) :]]


def _unwrap_optional(annotation: Any) -> Any:
    """Strip ``Optional[T]`` / ``T | None`` down to ``T``; pass through otherwise.

    Args:
        annotation: A pydantic field annotation, possibly a two-armed
            union with ``None``.

    Returns:
        The non-``None`` member of a two-armed union, or ``annotation``
        unchanged.
    """
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _list_item_type(annotation: Any) -> Any | None:
    """Return ``T`` for ``list[T]`` / ``Optional[list[T]]`` annotations, else None.

    Args:
        annotation: A pydantic field annotation to inspect.

    Returns:
        The list's item type, or ``None`` if ``annotation`` is not a list
        (optionally wrapped in ``Optional``) with exactly one type argument.
    """
    annotation = _unwrap_optional(annotation)
    if typing.get_origin(annotation) is list:
        args = typing.get_args(annotation)
        return args[0] if len(args) == 1 else None
    return None


def _is_model(annotation: Any) -> bool:
    """Return whether ``annotation`` is a pydantic model class.

    Args:
        annotation: A (possibly unwrapped) field annotation to test.

    Returns:
        ``True`` if ``annotation`` is a class carrying pydantic's
        ``model_fields``.
    """
    return isinstance(annotation, type) and hasattr(annotation, "model_fields")


def _assert_fields_resolve(
    model_cls: Any,
    doc: dict[str, Any],
    properties: dict[str, Any],
    path: str,
) -> set[str]:
    """Recursively assert every field declared on ``model_cls`` has a matching
    upstream property, following nested sections and list-of-model fields into
    the corresponding nested/`items` property tree.

    Args:
        model_cls: The pydantic model class whose declared fields are walked.
        doc: The full schema document, providing the ``$defs`` lookup table
            used to resolve ``$ref`` properties.
        properties: The upstream schema's property mapping at this nesting
            level.
        path: The dotted field path walked so far; empty at the root.

    Returns:
        The set of full field paths walked, including every nested section
        and list-item path reached. A model declaring no fields walks
        nothing, so callers must check the returned set against an expected
        minimum rather than treat a clean pass alone as proof of coverage.
    """
    walked: set[str] = set()
    for field_name, field_info in model_cls.model_fields.items():
        full_path = f"{path}.{field_name}" if path else field_name
        assert field_name in properties, f"{full_path} has no matching upstream property"
        walked.add(full_path)

        annotation = _unwrap_optional(field_info.annotation)
        item_type = _list_item_type(field_info.annotation)
        if _is_model(annotation):
            sub = _resolve(doc, properties[field_name])
            walked |= _assert_fields_resolve(annotation, doc, sub.get("properties", {}), full_path)
        elif item_type is not None and _is_model(item_type):
            sub = _resolve(doc, properties[field_name])
            items = _resolve(doc, sub.get("items", {}))
            walked |= _assert_fields_resolve(item_type, doc, items.get("properties", {}), f"{full_path}[]")
    return walked


def test_provenance_fields_resolve_upstream() -> None:
    """Provenance fields (source_file, source_type, ...) exist in the pinned
    shared provenance module (``modules/common/provenance.schema.json``),
    and the walk covers at least the fields BDF's records write into
    ``provenance``."""
    from bdf.battinfo_records import Provenance

    doc = _load_schema("provenance.schema.json")

    walked = _assert_fields_resolve(Provenance, doc, doc.get("properties", {}), "provenance")

    expected = {"provenance.source_file", "provenance.source_type"}
    assert expected <= walked


def test_test_record_fields_resolve_upstream() -> None:
    """Every field path TestRecord declares (schema_version, test.*,
    provenance and its nested contents) is a valid location in the pinned
    test.schema.json, validated against that schema's own inline
    ``provenance`` definition (not the shared module, which is a distinct
    definition covered separately by
    ``test_provenance_fields_resolve_upstream``), and the walk covers at
    least the fields the test-record model declares."""
    from bdf.battinfo_records import TestRecord

    doc = _load_schema("test.schema.json")

    walked = _assert_fields_resolve(
        TestRecord,
        doc,
        doc["properties"],
        "",
    )

    expected = {
        "schema_version",
        "test.name",
        "test.instrument_name",
        "test.started_at",
        "test.ended_at",
        "provenance.source_file",
        "provenance.source_type",
    }
    assert expected <= walked


def test_dataset_record_fields_resolve_upstream() -> None:
    """Every field path DatasetRecord declares (schema_version, dataset.*
    including variable_measured's item shape, provenance and its nested
    contents) is a valid location in the pinned dataset.schema.json,
    validated against that schema's own inline ``provenance`` definition
    (not the shared module, which is a distinct definition covered
    separately by ``test_provenance_fields_resolve_upstream``), and the walk
    covers at least the fields the dataset-record model declares."""
    from bdf.battinfo_records import DatasetRecord

    doc = _load_schema("dataset.schema.json")

    walked = _assert_fields_resolve(
        DatasetRecord,
        doc,
        doc["properties"],
        "",
    )

    expected = {
        "schema_version",
        "dataset.variable_measured",
        "dataset.variable_measured[].name",
        "dataset.variable_measured[].unit_text",
        "dataset.variable_measured[].same_as",
        "dataset.variable_measured[].description",
        "provenance.source_file",
        "provenance.source_type",
    }
    assert expected <= walked


def test_no_runtime_battinfo_import() -> None:
    """BDF must never import battinfo at runtime, keeping the
    battinfo[processing] -> batterydf dependency direction acyclic: the
    module source carries no import naming it, and a fresh record's
    ``to_dict()`` serialisation carries no trace of it either."""
    import ast
    import inspect

    import bdf.battinfo_records as battinfo_records

    tree = ast.parse(inspect.getsource(battinfo_records))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "battinfo" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or node.module.split(".")[0] != "battinfo"

    dumped = battinfo_records.ReadMetadata().to_dict()
    assert isinstance(dumped, dict)
    assert "battinfo" not in json.dumps(dumped)


def test_read_metadata_exposes_no_importer_adapter() -> None:
    """The serialised ``ReadMetadata`` is the handoff contract itself: the
    only public method BDF adds is ``to_dict()`` — no ``to_battinfo`` or
    other adapter producing importer keyword arguments."""
    import pydantic

    from bdf.battinfo_records import ReadMetadata

    def _public_methods(cls: type) -> set[str]:
        return {name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name))}

    own = _public_methods(ReadMetadata) - _public_methods(pydantic.BaseModel)
    assert own == {"to_dict"}
