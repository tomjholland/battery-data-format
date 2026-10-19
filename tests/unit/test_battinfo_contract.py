"""Contract test: BDF stays free of a run-time BattINFO import, and the
bundled upstream BattINFO schemas (``src/bdf/data/battinfo/``) stay in step
with what ``scripts/update_battinfo.py`` fetches, writes, and stamps.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pydantic
import pytest

import bdf.metadata
from bdf.metadata import Metadata

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.update_battinfo import (  # noqa: E402
    DEFAULT_BRANCH,
    compare,
    fetch_schemas,
    load_managed,
    load_schema,
    load_version,
    main,
    normalize,
    resolve_ref,
    update,
    write_bundle,
)


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
# Bundled schema snapshot (package data), mirroring the ontology snapshot
# (``bdf.spec.ColumnOntology.build`` / ``.get_snapshot``)
# ---------------------------------------------------------------------------

# The managed schema files, keyed by their bundle-relative path, mirroring
# scripts.update_battinfo.MANAGED_SCHEMA_PATHS.
_MOCK_MANAGED_SCHEMA_CONTENT: dict[str, bytes] = {
    "test.schema.json": b'{"title": "test schema", "properties": {}}',
    "cell-instance.schema.json": b'{"title": "cell-instance schema", "properties": {}}',
    "cell-canonical.schema.json": b'{"title": "cell-canonical schema", "properties": {}}',
    "channel.schema.json": b'{"title": "channel schema", "properties": {}}',
    "equipment.schema.json": b'{"title": "equipment schema", "properties": {}}',
    "test-protocol.schema.json": b'{"title": "test-protocol schema", "properties": {}}',
    "dataset.schema.json": b'{"title": "dataset schema", "properties": {}}',
    "modules/common/quantitative-properties.schema.json": b'{"title": "quantitative-properties schema"}',
    "modules/common/quantity.schema.json": b'{"title": "quantity schema", "properties": {}}',
}


def test_bundle_loads_the_managed_schemas_and_version_stamp() -> None:
    """The package-data loader reads the bundle under ``bdf/data/battinfo/``
    and exposes every managed schema file, and a ``VERSION`` stamp carrying a
    commit ref."""
    managed = load_managed()

    assert set(managed) == set(_MOCK_MANAGED_SCHEMA_CONTENT)
    for rel_name, schema in managed.items():
        assert isinstance(schema, dict) and schema, f"bundled schema {rel_name} is empty"
    assert load_version()["ref"], "VERSION stamp carries no commit ref"


def test_load_schema_reads_one_managed_file_and_rejects_any_other() -> None:
    """The by-name loader serves a managed file and refuses a name the bundle
    does not manage, so a typo fails at the call rather than at the read."""
    assert load_schema("test.schema.json")["title"]

    with pytest.raises(KeyError):
        load_schema("not-a-managed-file.schema.json")


def _mock_fetch_responses(overrides: dict[str, bytes] | None = None) -> list[Mock]:
    """Return one mocked HTTP response per managed schema file, in fetch order.

    Args:
        overrides: Content to serve instead of the default for the named files.
            Use it to stand in for an upstream edit.
    """
    overrides = overrides or {}
    responses = []
    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        response = Mock()
        response.content = overrides.get(rel_name, content)
        response.raise_for_status = Mock()
        responses.append(response)
    return responses


def _bundle_the_mock_schemas(tmp_path: Path) -> Path:
    """Write the mock schema content as a bundle and return its schema directory."""
    return write_bundle(dict(_MOCK_MANAGED_SCHEMA_CONTENT), "0" * 40, tmp_path / "bundle") / "schemas"


def test_update_script_fetches_writes_and_stamps(tmp_path: Path) -> None:
    """The update script fetches the eight managed schema files at a given ref
    over a mocked transport (ontology-snapshot style), writes them atomically
    under ``schemas/``, and stamps ``VERSION`` with that ref."""
    dest = tmp_path / "battinfo"
    ref = "deadbeef1234"
    with patch("requests.get", side_effect=_mock_fetch_responses()) as mock_get:
        result = update(ref, dest=dest)
        assert mock_get.call_count == len(_MOCK_MANAGED_SCHEMA_CONTENT)

    for call in mock_get.call_args_list:
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url is not None and ref in url, f"fetch URL {url!r} does not carry the requested ref {ref!r}"

    assert result == dest
    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        assert json.loads((dest / "schemas" / rel_name).read_bytes()) == json.loads(content)
    assert not list(dest.rglob("*.tmp")), "the update left a temporary file behind"

    version_text = (dest / "VERSION").read_text(encoding="utf-8")
    assert f"ref={ref}" in version_text


def test_write_bundle_normalizes_what_it_writes(tmp_path: Path) -> None:
    """The bundle holds the upstream document and not the upstream byte layout.
    Every file lands with sorted keys, a two-space indent, and a trailing
    newline, so an upstream commit that only reorders keys produces no diff."""
    schemas = _bundle_the_mock_schemas(tmp_path)

    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        written = (schemas / rel_name).read_bytes()
        assert written == normalize(content)
        assert json.loads(written) == json.loads(content)
        assert written.endswith(b"\n")

    text = (schemas / "test.schema.json").read_text(encoding="utf-8")
    assert text.index('"properties"') < text.index('"title"'), "write_bundle did not sort the keys"
    assert '\n  "properties"' in text, "write_bundle did not indent by two spaces"


def test_write_bundle_is_stable_across_an_upstream_reorder(tmp_path: Path) -> None:
    """Two upstream layouts of one document write the same bytes."""
    reordered = {
        rel_name: json.dumps(dict(reversed(list(json.loads(content).items()))), indent=8).encode()
        for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items()
    }
    first = _bundle_the_mock_schemas(tmp_path / "first")
    second = write_bundle(reordered, "0" * 40, tmp_path / "second" / "bundle") / "schemas"

    for rel_name in _MOCK_MANAGED_SCHEMA_CONTENT:
        assert (first / rel_name).read_bytes() == (second / rel_name).read_bytes()


def test_compare_reports_nothing_when_upstream_matches_the_bundle(tmp_path: Path) -> None:
    """The sync trigger reads content, not the pinned commit. An upstream commit
    that leaves the eight managed files alone reports no difference, so an
    unrelated change upstream opens no pull request."""
    source = _bundle_the_mock_schemas(tmp_path)

    with patch("requests.get", side_effect=_mock_fetch_responses()):
        assert compare("main", source=source) == {}


def test_compare_reports_only_the_files_whose_content_changed(tmp_path: Path) -> None:
    """A comparison names the managed files that moved and no others, so the
    sync pull request states what actually changed upstream."""
    source = _bundle_the_mock_schemas(tmp_path)
    edited = b'{"title": "test schema", "properties": {"schema_version": {"type": "string"}}}'

    with patch("requests.get", side_effect=_mock_fetch_responses({"test.schema.json": edited})):
        differing = compare("main", source=source)

    assert set(differing) == {"test.schema.json"}
    assert differing["test.schema.json"] == edited


def test_compare_ignores_a_reformat_that_leaves_the_content_equal(tmp_path: Path) -> None:
    """The comparison parses before it compares. An upstream commit that only
    reindents a managed file reports no difference."""
    source = _bundle_the_mock_schemas(tmp_path)
    reformatted = b'{\n  "title": "test schema",\n  "properties": {}\n}\n'

    with patch("requests.get", side_effect=_mock_fetch_responses({"test.schema.json": reformatted})):
        assert compare("main", source=source) == {}


def test_resolve_ref_returns_the_commit_a_branch_names() -> None:
    """The stamp records a commit and never a branch, because a branch moves.
    The resolution turns the branch the sync job reads into that commit."""
    sha = "a" * 40
    response = Mock(raise_for_status=Mock())
    response.json.return_value = {"sha": sha}

    with patch("requests.get", return_value=response) as mock_get:
        assert resolve_ref("main") == sha

    url = mock_get.call_args.args[0]
    assert url.endswith("/commits/main"), f"resolution URL {url!r} does not name the requested ref"


def test_resolve_ref_raises_runtime_error_when_the_response_carries_no_sha() -> None:
    """A resolution that returns no commit fails at the call. A stamp written
    from a missing SHA would name a ref that no test can refetch."""
    response = Mock(raise_for_status=Mock())
    response.json.return_value = {}

    with patch("requests.get", return_value=response), pytest.raises(RuntimeError, match="no commit SHA"):
        resolve_ref("main")


def test_check_mode_fails_and_writes_nothing_when_the_bundle_is_stale(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check`` reports the files that moved, exits non-zero so the pull
    request check fails, and never touches the bundle. The exit code matches
    ``--check`` on ``scripts/generate_docs.py``."""
    sha = "b" * 40
    with (
        patch("scripts.update_battinfo.resolve_ref", return_value=sha),
        patch("scripts.update_battinfo.compare", return_value={"test.schema.json": b"{}"}),
        patch("scripts.update_battinfo.write_bundle") as mock_write,
        pytest.raises(SystemExit) as excinfo,
    ):
        main(["--check"])

    assert excinfo.value.code != 0
    assert sha in str(excinfo.value), "the failure does not name the ref to refresh from"
    assert capsys.readouterr().out.splitlines() == [
        "changed=true",
        f"ref={sha}",
        "files=test.schema.json",
    ]
    mock_write.assert_not_called()


def test_check_mode_reports_no_change_when_the_bundle_is_current(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A current bundle reports ``changed=false`` with an empty file list and
    exits zero, so the pull request check passes."""
    sha = "c" * 40
    with (
        patch("scripts.update_battinfo.resolve_ref", return_value=sha),
        patch("scripts.update_battinfo.compare", return_value={}),
    ):
        main(["--check"])

    assert capsys.readouterr().out.splitlines() == ["changed=false", f"ref={sha}", "files="]


def test_update_mode_stamps_the_commit_a_branch_resolves_to() -> None:
    """A caller that names a branch still stamps a commit, because the entry
    point resolves the ref before it writes."""
    sha = "d" * 40
    with (
        patch("scripts.update_battinfo.resolve_ref", return_value=sha) as mock_resolve,
        patch("scripts.update_battinfo.update") as mock_update,
    ):
        main(["main"])

    mock_resolve.assert_called_once_with("main")
    mock_update.assert_called_once_with(sha)


@pytest.mark.network
@pytest.mark.live_network
@pytest.mark.freshness
def test_bundled_snapshot_matches_its_declared_release(tmp_path: Path) -> None:
    """The stamp never lies about the bundle: fetching the eight managed
    schema files at the ref the bundled ``VERSION`` stamp declares reproduces
    the bundled files exactly."""
    ref = load_version()["ref"]
    fresh_dir = write_bundle(fetch_schemas(ref), ref, tmp_path / "fresh")

    assert load_managed(fresh_dir / "schemas") == load_managed()


@pytest.mark.network
@pytest.mark.live_network
@pytest.mark.freshness
def test_bundle_matches_the_upstream_default_branch() -> None:
    """The bundle never ages silently. The stamp alone cannot catch that: it
    pins a commit, and a refetch at a commit reproduces the bundle however far
    upstream moves. This test reads upstream ``main`` instead.

    The comparison reads parsed content, so an upstream commit that reformats
    one of the eight files, or that leaves ``assets/schemas/`` alone, keeps
    this green. Only a real schema change fails it."""
    ref = resolve_ref(DEFAULT_BRANCH)
    differing = compare(ref)

    assert not differing, (
        f"the bundled BattINFO schemas differ from {DEFAULT_BRANCH} at {ref}: "
        f"{', '.join(sorted(differing))}. Run scripts/update_battinfo.py {ref} to refresh the bundle."
    )


def test_missing_bundle_raises_runtime_error_naming_the_update_script(tmp_path: Path) -> None:
    """A missing schema directory raises ``RuntimeError`` naming the update
    script, as the ontology snapshot does for its own bundle."""
    with pytest.raises(RuntimeError, match="update_battinfo"):
        load_managed(tmp_path / "does-not-exist")


def test_unparseable_bundle_raises_runtime_error_naming_the_update_script(tmp_path: Path) -> None:
    """A schema directory whose file is not valid JSON raises ``RuntimeError``
    naming the update script."""
    schemas = tmp_path / "schemas"
    (schemas / "modules" / "common").mkdir(parents=True)
    (schemas / "test.schema.json").write_text("not valid json", encoding="utf-8")
    for rel_name in _MOCK_MANAGED_SCHEMA_CONTENT:
        if rel_name != "test.schema.json":
            (schemas / rel_name).write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="update_battinfo"):
        load_managed(schemas)

    with pytest.raises(RuntimeError, match="update_battinfo"):
        load_version(tmp_path)


def test_fetch_raises_http_error_on_fetch_failure() -> None:
    """The fetch propagates ``requests.HTTPError`` when a schema fetch fails."""
    import requests

    response = Mock()
    response.raise_for_status = Mock(side_effect=requests.HTTPError("fetch failed"))

    with patch("requests.get", return_value=response), pytest.raises(requests.HTTPError):
        fetch_schemas("deadbeef1234")


def test_fetch_raises_runtime_error_on_invalid_json() -> None:
    """The fetch raises ``RuntimeError`` when fetched content is not valid JSON."""
    response = Mock()
    response.content = b"not valid json"
    response.raise_for_status = Mock()

    with patch("requests.get", return_value=response), pytest.raises(RuntimeError):
        fetch_schemas("deadbeef1234")
