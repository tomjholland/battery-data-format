"""Tests for fetching and caching a BattINFO schema set at an upstream commit."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bdf.battinfo._fetch import MANAGED_SCHEMA_PATHS
from bdf.battinfo.schema import BattinfoSchema

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.update_battinfo import (  # noqa: E402
    MANAGED_SCHEMA_PATHS as SCRIPT_MANAGED_SCHEMA_PATHS,
)

_REF = "0b4dfd3fe70ccf2e6cc68e23aebb3fc8d9120c88"

# Mirrors `scripts.update_battinfo.MANAGED_SCHEMA_PATHS`, one mock body per
# managed schema file.
_MOCK_MANAGED_SCHEMA_CONTENT: dict[str, bytes] = {
    "test.schema.json": b'{"$id": "https://example.test/test.schema.json", "properties": {}}',
    "cell-instance.schema.json": b'{"$id": "https://example.test/cell-instance.schema.json", "properties": {}}',
    "cell-canonical.schema.json": b'{"$id": "https://example.test/cell-canonical.schema.json", "properties": {}}',
    "channel.schema.json": b'{"$id": "https://example.test/channel.schema.json", "properties": {}}',
    "equipment.schema.json": b'{"$id": "https://example.test/equipment.schema.json", "properties": {}}',
    "test-protocol.schema.json": b'{"$id": "https://example.test/test-protocol.schema.json", "properties": {}}',
    "dataset.schema.json": b'{"$id": "https://example.test/dataset.schema.json", "properties": {}}',
    "modules/common/quantitative-properties.schema.json": (
        b'{"$id": "https://example.test/quantitative-properties.schema.json"}'
    ),
    "modules/common/quantity.schema.json": b'{"$id": "https://example.test/quantity.schema.json", "properties": {}}',
}


def _mock_responses(overrides: dict[str, bytes] | None = None) -> list[Mock]:
    """Return one mocked HTTP response per managed schema file, in fetch order.

    Args:
        overrides: Content to serve instead of the default for the named
            files, used to stand in for a broken or missing upstream file.
    """
    overrides = overrides or {}
    responses = []
    for rel_name, content in _MOCK_MANAGED_SCHEMA_CONTENT.items():
        response = Mock()
        response.content = overrides.get(rel_name, content)
        response.raise_for_status = Mock()
        responses.append(response)
    return responses


class TestFetch:
    def test_fetch_at_a_commit_loads_that_schema_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """battinfo-schema: A fetch at a commit loads that schema set.

        The loaded set holds the managed documents at that commit, and
        reports that commit.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        with patch("requests.get", side_effect=_mock_responses()) as mock_get:
            schema = BattinfoSchema.from_ref(_REF)
        assert isinstance(schema, BattinfoSchema)
        assert schema.ref == _REF
        assert mock_get.call_count == len(MANAGED_SCHEMA_PATHS)

    def test_a_branch_name_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """battinfo-schema: A branch name is refused.

        A branch or a tag raises before any request, and the message names
        the refused value.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        with patch("requests.get") as mock_get:
            with pytest.raises(ValueError, match="main"):
                BattinfoSchema.from_ref("main")
            mock_get.assert_not_called()

    def test_an_unparseable_document_fails_the_fetch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """battinfo-schema: An unparseable document fails the fetch.

        A fetched document that is not valid JSON raises, naming that
        document, and the fetch caches nothing.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        broken = _mock_responses({"test.schema.json": b"not json"})
        with patch("requests.get", side_effect=broken), pytest.raises(Exception, match="test.schema.json"):
            BattinfoSchema.from_ref(_REF)
        assert list(tmp_path.rglob("*")) == []

    def test_a_document_that_exceeds_the_size_cap_fails_the_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """battinfo-schema: An unparseable document fails the fetch.

        A fetched document that exceeds the declared size cap raises,
        naming that document, and the fetch caches nothing.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        oversized = json.dumps({"padding": "x" * (20 * 1024 * 1024)}).encode()
        too_big = _mock_responses({"test.schema.json": oversized})
        with patch("requests.get", side_effect=too_big), pytest.raises(Exception, match="test.schema.json"):
            BattinfoSchema.from_ref(_REF)
        assert list(tmp_path.rglob("*")) == []

    def test_a_second_load_of_the_same_commit_reads_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """battinfo-schema: A second load of the same commit reads the cache.

        The second load makes no request, and returns the same documents.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        with patch("requests.get", side_effect=_mock_responses()) as mock_get:
            BattinfoSchema.from_ref(_REF)
        first_call_count = mock_get.call_count
        with patch("requests.get", side_effect=_mock_responses()) as second_get:
            BattinfoSchema.from_ref(_REF)
        second_get.assert_not_called()
        assert first_call_count == len(MANAGED_SCHEMA_PATHS)

    def test_a_refresh_refetches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """battinfo-schema: A refresh refetches.

        A caller that asks to refresh a cached commit runs the fetch again,
        and the cache holds the refetched documents.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        with patch("requests.get", side_effect=_mock_responses()):
            BattinfoSchema.from_ref(_REF)
        with patch("requests.get", side_effect=_mock_responses()) as second_get:
            BattinfoSchema.from_ref(_REF, refresh=True)
        assert second_get.call_count == len(MANAGED_SCHEMA_PATHS)

    def test_the_cache_honours_the_cache_directory_setting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """battinfo-schema: The cache honours the cache directory setting.

        A fetched schema set is written under the configured cache
        directory.
        """
        chosen = tmp_path / "chosen-cache"
        monkeypatch.setenv("BDF_CACHE_DIR", str(chosen))
        with patch("requests.get", side_effect=_mock_responses()):
            BattinfoSchema.from_ref(_REF)
        assert any(chosen.rglob("*"))

    def test_an_interrupted_fetch_leaves_no_partial_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """battinfo-schema: An interrupted fetch leaves no partial set.

        A fetch that fails after it wrote some documents leaves the cache
        holding no set for that commit.
        """
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        responses = _mock_responses()
        responses[-1] = Mock(raise_for_status=Mock(side_effect=RuntimeError("connection reset")))
        with patch("requests.get", side_effect=responses), pytest.raises(Exception, match="connection reset"):
            BattinfoSchema.from_ref(_REF)
        assert list(tmp_path.rglob("*")) == []

    def test_the_script_and_the_package_name_the_same_paths(self) -> None:
        """battinfo-schema: The script and the package name the same paths."""
        assert MANAGED_SCHEMA_PATHS == SCRIPT_MANAGED_SCHEMA_PATHS

    @pytest.mark.network
    @pytest.mark.live_network
    def test_fetch_the_bundled_commit_for_real(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A live fetch of the bundled commit loads that schema set."""
        monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path))
        schema = BattinfoSchema.from_ref(_REF)
        assert isinstance(schema, BattinfoSchema)
        assert schema.ref == _REF
