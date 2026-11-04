"""Tests of the BattINFO runtime schema set: its source, its download, and the check it gives."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import referencing.exceptions

import bdf.battinfo
from bdf._errors import BDFDefinitionError
from bdf.battinfo import BATTINFO_SOURCE, ENTITY_DOCS, BattinfoSchema, _download_battinfo, _update_snapshot_cli
from bdf.metadata import Metadata

from ._battinfo import (
    BUNDLED,
    RAW_URL,
    FakeRawGitHub,
    battinfo_cache,  # noqa: F401
    bundled_docs,
    cache_release,
    point_bundle_at,
    release_docs,
    write_release,
)

pytestmark = pytest.mark.usefixtures("battinfo_cache")
not_implemented = pytest.mark.xfail(strict=True, reason="BattINFO runtime schema not implemented")

DRAFT = "https://json-schema.org/draft/2020-12/schema"
UNIT_TEXT = {"value": 25, "unit_text": "degC"}


# --- the source ---------------------------------------------------------------------------------


@not_implemented
def test_the_bundled_set_is_the_release_the_version_file_names() -> None:
    schema = bdf.battinfo.BATTINFO_SCHEMA
    assert schema.version == (BUNDLED / "VERSION").read_text().strip()
    assert set(ENTITY_DOCS.values()) <= set(schema.docs)


@not_implemented
def test_from_version_gives_a_new_set_and_caches_a_fetched_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("requests.get", FakeRawGitHub(tag="v0.9.0"))
    bundled = bdf.battinfo.BATTINFO_SCHEMA

    fetched = BattinfoSchema.from_version("0.9.0")

    assert fetched.version == "0.9.0"
    assert (tmp_path / "cache" / "battinfo-v0.9.0" / "VERSION").read_text() == "0.9.0\n"
    assert BattinfoSchema.from_version(bundled.version) is not bundled
    assert bdf.battinfo.BATTINFO_SCHEMA is bundled


@not_implemented
def test_a_release_that_breaks_the_metaschema_does_not_load(tmp_path: Path) -> None:
    broken = {"test.schema.json": {"$id": "https://example.test/test.schema.json", "minimum": "5"}}
    cache_release(tmp_path, "0.9.0", broken)

    with pytest.raises(BDFDefinitionError, match="0.9.0") as info:
        BattinfoSchema.from_version("0.9.0")

    assert isinstance(info.value.__cause__, jsonschema.exceptions.SchemaError)


@not_implemented
def test_the_update_command_writes_a_release_into_the_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = write_release(tmp_path / "bundle", "0.7.0", release_docs("legacy_field"))
    monkeypatch.setattr(BATTINFO_SOURCE, "bundled", bundle)
    monkeypatch.setattr("requests.get", FakeRawGitHub(tag="v0.8.0"))
    monkeypatch.setattr(sys, "argv", ["bdf-update-battinfo", "0.8.0"])

    _update_snapshot_cli()

    written = {p.relative_to(bundle / "schemas").as_posix() for p in (bundle / "schemas").rglob("*.json")}
    assert written == set(bundled_docs())
    assert (bundle / "VERSION").read_text() == "0.8.0\n"


@not_implemented
def test_the_update_command_needs_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRawGitHub()
    monkeypatch.setattr("requests.get", fake)
    monkeypatch.setattr(sys, "argv", ["bdf-update-battinfo"])

    with pytest.raises(BDFDefinitionError, match="no latest release"):
        _update_snapshot_cli()

    assert fake.urls == []


# --- the download -------------------------------------------------------------------------------


def _fetchable_docs() -> dict[str, dict[str, Any]]:
    """Six entity documents; two of them refer to one document, which refers up to another."""
    docs: dict[str, dict[str, Any]] = {path: {"$id": f"https://example.test/{path}"} for path in ENTITY_DOCS.values()}
    docs["test.schema.json"]["description"] = "µ"
    docs["test.schema.json"]["properties"] = {
        "a": {"$ref": "modules/common/quantity.schema.json"},
        "b": {"$ref": "#/$defs/B"},
    }
    docs["cell-instance.schema.json"]["properties"] = {"a": {"$ref": "modules/common/quantity.schema.json#/$defs/Q"}}
    docs["modules/common/quantity.schema.json"] = {"$id": "https://example.test/q", "$ref": "../units.schema.json"}
    docs["modules/units.schema.json"] = {"$id": "https://example.test/units"}
    return docs


@not_implemented
def test_a_download_fetches_each_document_that_a_ref_reaches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _fetchable_docs()
    fake = FakeRawGitHub(docs)
    monkeypatch.setattr("requests.get", fake)
    dest = tmp_path / "out"
    dest.mkdir()

    _download_battinfo("v0.9.0", dest, 30.0)

    assert sorted(fake.urls) == sorted(RAW_URL.format(tag="v0.9.0", path=path) for path in docs)
    for path, doc in docs.items():
        text = (dest / "schemas" / path).read_text()
        assert text == json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


@not_implemented
@pytest.mark.parametrize("ref", ["https://example.com/q.schema.json", "/q.schema.json", "../q.schema.json"])
def test_a_download_refuses_a_ref_that_leaves_the_schemas(
    ref: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = _fetchable_docs()
    docs["test.schema.json"]["properties"] = {"a": {"$ref": ref}}
    monkeypatch.setattr("requests.get", FakeRawGitHub(docs))

    with pytest.raises(ValueError):
        _download_battinfo("v0.9.0", tmp_path, 30.0)


@not_implemented
def test_a_download_gives_each_request_the_time_that_is_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    fake = FakeRawGitHub(_fetchable_docs())

    def slow_get(url: str, **kwargs: Any) -> Any:
        try:
            return fake(url, **kwargs)
        finally:
            now[0] += 3.0

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr("requests.get", slow_get)

    with pytest.raises(TimeoutError):
        _download_battinfo("v0.9.0", tmp_path, 5.0)

    assert fake.timeouts == [pytest.approx(5.0), pytest.approx(2.0)]


# --- the check ----------------------------------------------------------------------------------


def _subschemas(schema: Any, keywords: tuple[str, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[str, ...]]]:
    """Yield each subschema with the keywords on its path from the document, without following ``$ref``."""
    if not isinstance(schema, dict):
        return
    yield schema, keywords
    for key, value in schema.items():
        if key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            children = list(value.values())
        elif key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = list(value)
        elif key in ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"):
            children = [value]
        else:
            continue
        for child in children:
            yield from _subschemas(child, (*keywords, key))


def test_the_bundle_keeps_the_rule_that_a_validator_may_ignore_a_records_required() -> None:
    for name, doc in bundled_docs().items():
        for sub, keywords in _subschemas(doc):
            where = f"{name} at {'/'.join(keywords)}"
            if "required" in sub and "properties" not in sub:
                assert {"anyOf", "oneOf", "not"} & set(keywords), where
            if "required" in sub and "properties" in sub:
                assert not {"if", "not", "contains", "dependentSchemas"} & set(keywords), where
            assert sum("properties" in branch for branch in sub.get("oneOf", [])) < 2, where


@not_implemented
def test_a_partial_record_is_valid_also_in_a_document_that_a_ref_reaches() -> None:
    inner: dict[str, Any] = {"$id": "https://example.test/b.schema.json", "$schema": DRAFT, "required": ["a"]}
    inner["properties"] = {"a": {"type": "string"}}
    outer: dict[str, Any] = {"$id": "https://example.test/test.schema.json", "$schema": DRAFT, "required": ["x"]}
    outer["properties"] = {"x": {"$ref": "b.schema.json"}}
    schema = BattinfoSchema({"test.schema.json": outer, "b.schema.json": inner}, "0.1.0")

    assert schema.refusals("battinfo_test", {"x": {}}) == []
    assert len(schema.refusals("battinfo_test", {"x": {"a": 1}})) == 1


@not_implemented
def test_a_quantity_with_a_value_and_no_unit_is_refused() -> None:
    schema = bdf.battinfo.BATTINFO_SCHEMA
    test = {"test": {"conditions": {"ambient_temperature": {"value": 25}}}}

    assert any(
        "battinfo_test.test.conditions.ambient_temperature" in line for line in schema.refusals("battinfo_test", test)
    )


@not_implemented
def test_a_map_keeps_the_keys_that_its_schema_declares() -> None:
    schema = bdf.battinfo.BATTINFO_SCHEMA

    assert schema.refusals("battinfo_test", {"test": {"conditions": {"ambient_temperature": UNIT_TEXT}}}) == []
    (line,) = schema.refusals("battinfo_test", {"test": {"conditions": {"Bad Key": UNIT_TEXT}}})
    assert "battinfo_test.test.conditions" in line
    assert "Bad Key" in line


@not_implemented
def test_a_choice_between_schemas_is_decided_as_a_whole() -> None:
    schema = bdf.battinfo.BATTINFO_SCHEMA

    assert schema.refusals("battinfo_dataset", {"dataset": {"main_entity": [{"url": "data.csv"}]}}) == []
    (line,) = schema.refusals("battinfo_dataset", {"dataset": {"main_entity": {"url": "data.csv", "bogus": 1}}})
    assert "battinfo_dataset.dataset.main_entity" in line
    assert "'bogus' was unexpected" in line


@not_implemented
def test_an_unknown_key_is_refused_where_the_schema_closes_the_object() -> None:
    (line,) = bdf.battinfo.BATTINFO_SCHEMA.refusals("battinfo_test", {"test": {"nmae": "cell-1"}})

    assert "battinfo_test.test:" in line
    assert "'nmae' was unexpected" in line


@not_implemented
def test_an_empty_entity_needs_no_document_and_data_needs_one() -> None:
    schema = BattinfoSchema({}, "0.1.0")

    assert schema.refusals("battinfo_test", {}) == []
    with pytest.raises(BDFDefinitionError, match="test.schema.json"):
        schema.refusals("battinfo_test", {"test": {}})


@not_implemented
def test_a_refused_value_can_be_assigned_and_check_names_it() -> None:
    meta = Metadata()
    meta.battinfo_test = {"test": {"started_at": "yesterday"}}  # type: ignore[assignment]

    release = bdf.battinfo.BATTINFO_SCHEMA.version
    assert meta.check() == [
        f"BattINFO {release} refuses battinfo_test.test.started_at: 'yesterday' is not of type 'integer'"
    ]


@not_implemented
def test_a_partial_record_gives_no_lines() -> None:
    meta = Metadata()
    meta.battinfo_test = {"test": {"name": "cell-1"}}  # type: ignore[assignment]

    assert meta.check() == []


@not_implemented
def test_a_set_that_cannot_be_used_raises() -> None:
    doc = {"$id": "https://example.test/test.schema.json", "properties": {"test": {"$ref": "missing.schema.json"}}}
    meta = Metadata()
    meta.battinfo_test = {"test": {}}  # type: ignore[assignment]

    with pytest.raises(referencing.exceptions.Unresolvable):
        meta.check(BattinfoSchema({"test.schema.json": doc}, "0.1.0"))


@not_implemented
def test_check_without_a_set_uses_the_release_the_metadata_states(tmp_path: Path) -> None:
    cache_release(tmp_path, "0.7.0", release_docs("legacy_field"))
    meta = Metadata(bdf={"battinfo_version": "0.7.0"})  # type: ignore[arg-type]
    meta.battinfo_test = {"test": {"legacy_field": 1}}  # type: ignore[assignment]

    assert meta.check() == []
    assert any("'legacy_field' was unexpected" in line for line in meta.check(bdf.battinfo.BATTINFO_SCHEMA))


@not_implemented
def test_check_reads_a_0_2_0_sidecars_ref_as_release_0_8_0rc2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_release(tmp_path, "0.8.0rc2", bundled_docs())
    point_bundle_at(monkeypatch, tmp_path / "bundle", "0.9.0", release_docs("new_field"))
    meta = Metadata(bdf={"battinfo_ref": "2ea8929074a7430b6e437065686cda881cfc23e7"})  # type: ignore[arg-type]
    meta.battinfo_test = {"test": {"new_field": 1}}  # type: ignore[assignment]

    lines = meta.check()

    assert lines
    assert all(line.startswith("BattINFO 0.8.0rc2 refuses ") for line in lines)


@not_implemented
@pytest.mark.network
@pytest.mark.live_network
def test_a_fetched_bundled_release_is_the_bundle(tmp_path: Path) -> None:
    fetched = BATTINFO_SOURCE.write(tmp_path / "r", (BUNDLED / "VERSION").read_text().strip())

    def tree(root: Path) -> dict[str, bytes]:
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "schemas").rglob("*") if p.is_file()}

    assert tree(fetched) == tree(BUNDLED)
