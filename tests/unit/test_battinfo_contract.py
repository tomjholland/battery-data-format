"""Contract test: BDF stays free of a run-time BattINFO import, and the
bundled BattINFO snapshot (``src/bdf/data/battinfo/``) is what
``BattinfoSchema.get_snapshot`` writes for the release it names.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pydantic
import pytest
import requests

import bdf.metadata
from bdf.battinfo.schema import (
    MANAGED_SCHEMAS,
    BattinfoSchema,
    SchemaError,
    _update_snapshot_cli,
    bundled_schema_dir,
    bundled_snapshot_dir,
    load_bundled,
    load_docs,
)
from bdf.metadata import Metadata


def test_no_runtime_battinfo_import() -> None:
    """BDF must never import battinfo at runtime, keeping the
    battinfo[processing] -> batterydf dependency direction acyclic: the
    module source carries no import naming it, and a fresh record's
    ``to_dict()`` serialisation carries no trace of it either."""
    tree = ast.parse(inspect.getsource(bdf.metadata))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "battinfo" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or node.module.split(".")[0] != "battinfo"

    dumped = Metadata().to_dict()
    assert isinstance(dumped, dict)
    assert "battinfo" not in json.dumps(dumped)


def test_read_metadata_exposes_no_importer_adapter() -> None:
    """The serialised ``Metadata`` is the handoff contract itself: the
    only public method BDF adds is ``to_dict()`` — no ``to_battinfo`` or
    other adapter producing importer keyword arguments."""

    def _public_methods(cls: type) -> set[str]:
        return {name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name))}

    own = _public_methods(Metadata) - _public_methods(pydantic.BaseModel)
    assert own == {"to_dict"}


# ---------------------------------------------------------------------------
# Bundled snapshot (package data), mirroring the ontology snapshot
# (``bdf.spec.ColumnOntology.build`` / ``.get_snapshot``)
# ---------------------------------------------------------------------------


def _mock_document(name: str) -> dict[str, object]:
    """A minimal upstream document for one managed schema, keys deliberately unsorted."""
    return {"title": f"{name} schema", "$id": f"https://w3id.org/battinfo/schema/{name}", "properties": {}}


def _upstream(fail_on: str | None = None) -> Mock:
    """Mock ``requests.get``: serve each managed document by URL, or fail one.

    Args:
        fail_on: A managed document whose fetch answers with an HTTP error.
    """

    def _get(url: str, timeout: int) -> Mock:
        name = url.split("/assets/schemas/", 1)[1]
        response = Mock(content=json.dumps(_mock_document(name), indent=8).encode())
        error = requests.HTTPError(f"404 for {url}") if name == fail_on else None
        response.raise_for_status = Mock(side_effect=error)
        return response

    return Mock(side_effect=_get)


def test_the_bundle_holds_every_managed_document_and_names_its_release() -> None:
    """The bundled snapshot holds the managed documents alone, and its
    ``VERSION`` names the release the loaded set reports."""
    assert set(load_docs(bundled_schema_dir())) == set(MANAGED_SCHEMAS)
    version = (bundled_snapshot_dir() / "VERSION").read_text(encoding="utf-8").strip()
    assert version and load_bundled().version == version


def test_get_snapshot_fetches_each_document_at_the_release_tag(tmp_path: Path) -> None:
    """A snapshot reads every managed document at the release's ``v`` tag,
    writes each in the canonical form the pre-commit hook keeps, and names
    the release in ``VERSION``. A leading ``v`` names the same release."""
    get = _upstream()
    with patch("requests.get", get):
        dest = BattinfoSchema.get_snapshot("v0.8.0", tmp_path / "snapshot")

    urls = [call.args[0] for call in get.call_args_list]
    assert len(urls) == len(MANAGED_SCHEMAS)
    assert all("/BIG-MAP/BattINFO/v0.8.0/assets/schemas/" in url for url in urls)
    assert (dest / "VERSION").read_text(encoding="utf-8") == "0.8.0\n"
    for name in MANAGED_SCHEMAS:
        written = (dest / "schemas" / name).read_text(encoding="utf-8")
        assert written == json.dumps(_mock_document(name), indent=2, sort_keys=True) + "\n"
    assert [path.name for path in tmp_path.iterdir()] == ["snapshot"], "the fetch left a staging directory"
    assert BattinfoSchema.from_snapshot(dest).version == "0.8.0"


def test_a_failed_fetch_leaves_the_snapshot_as_it_was(tmp_path: Path) -> None:
    """A version upstream has not released answers 404, and the earlier
    snapshot survives untouched."""
    with patch("requests.get", _upstream()):
        dest = BattinfoSchema.get_snapshot("0.8.0", tmp_path / "snapshot")
    before = {path: path.read_bytes() for path in dest.rglob("*") if path.is_file()}

    with patch("requests.get", _upstream(fail_on="dataset.schema.json")), pytest.raises(requests.HTTPError):
        BattinfoSchema.get_snapshot("9.9.9", dest)

    assert {path: path.read_bytes() for path in dest.rglob("*") if path.is_file()} == before
    assert [path.name for path in tmp_path.iterdir()] == ["snapshot"]


def test_a_document_that_does_not_parse_is_refused(tmp_path: Path) -> None:
    """Fetched content that is not JSON raises ``SchemaError`` naming the document."""
    response = Mock(content=b"not valid json", raise_for_status=Mock())
    with patch("requests.get", return_value=response), pytest.raises(SchemaError, match="test.schema.json"):
        BattinfoSchema.get_snapshot("0.8.0", tmp_path / "snapshot")


def test_load_version_caches_the_release_and_refresh_refetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_version`` fetches a release once into ``BDF_CACHE_DIR`` and
    adopts it in place; a second load reads the cache, and ``refresh`` fetches
    again."""
    monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
    schema = load_bundled()

    with patch("requests.get", _upstream()) as first:
        schema.load_version("0.8.0")
    assert first.call_count == len(MANAGED_SCHEMAS)
    assert schema.version == "0.8.0"
    assert set(schema.docs) == set(MANAGED_SCHEMAS)
    assert (tmp_path / "battinfo-v0.8.0" / "VERSION").is_file()

    with patch("requests.get", _upstream()) as second:
        load_bundled().load_version("v0.8.0")
        second.assert_not_called()
        load_bundled().load_version("0.8.0", refresh=True)
    assert second.call_count == len(MANAGED_SCHEMAS)


def test_update_cli_writes_the_bundle_for_the_named_release() -> None:
    """``bdf-update-battinfo VERSION`` writes the bundled snapshot for that
    release, and needs the release named."""
    with patch("sys.argv", ["bdf-update-battinfo", "0.8.0"]), patch.object(BattinfoSchema, "get_snapshot") as snapshot:
        _update_snapshot_cli()
    snapshot.assert_called_once_with("0.8.0")

    with patch("sys.argv", ["bdf-update-battinfo"]), pytest.raises(SystemExit, match="VERSION"):
        _update_snapshot_cli()


@pytest.mark.network
@pytest.mark.live_network
@pytest.mark.freshness
def test_bundled_snapshot_matches_its_declared_release(tmp_path: Path) -> None:
    """The bundle never lies about its release: fetching the release
    ``VERSION`` names reproduces every bundled file byte for byte."""
    version = load_bundled().version
    assert version is not None
    fresh = BattinfoSchema.get_snapshot(version, tmp_path / "fresh")

    bundled = bundled_snapshot_dir()
    for name in ("VERSION", *(f"schemas/{doc}" for doc in MANAGED_SCHEMAS)):
        assert (fresh / name).read_bytes() == (bundled / name).read_bytes(), (
            f"bundled {name} differs from BattINFO release {version}. Run `bdf-update-battinfo {version}`."
        )
