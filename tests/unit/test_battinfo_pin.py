"""Tests for stamping the bundled BattINFO pin from upstream constants."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bdf.battinfo._pin import refresh

_REF = "5ff1a6f2590795765978bd8088e2e6a8c5c146f3"
_SCHEMA_VERSION_TEXT = 'SCHEMA_VERSION = "0.2.0"\n'
_BATTINFO_VERSION_TEXT = '__version__ = "0.7.0"\n'

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$")


def _mock_get(bundle_text: str = _SCHEMA_VERSION_TEXT, init_text: str = _BATTINFO_VERSION_TEXT) -> Mock:
    """Return a `requests.get` stand-in serving the two upstream constant files."""

    def _get(url: str, *args: object, **kwargs: object) -> Mock:
        response = Mock()
        response.raise_for_status = Mock()
        if "battinfo/bundle.py" in url:
            response.text = bundle_text
        elif "battinfo/__init__.py" in url:
            response.text = init_text
        else:
            response.raise_for_status = Mock(side_effect=AssertionError(f"unexpected request: {url}"))
        return response

    return Mock(side_effect=_get)


@pytest.mark.xfail(strict=True, reason="bdf.battinfo._pin not implemented")
def test_an_update_stamps_both_versions_from_upstream() -> None:
    """battinfo-bundle-pin: An update stamps both versions from upstream.

    The pin declares the repository, the resolved commit, the schema
    version, and the library version, and both version values equal the
    upstream constants at that commit.
    """
    with patch("requests.get", _mock_get()):
        pin = refresh(_REF)
    assert pin["repo"] == "BIG-MAP/BattINFO"
    assert pin["ref"] == _REF
    assert pin["schema_version"] == "0.2.0"
    assert pin["battinfo_version"] == "0.7.0"


@pytest.mark.xfail(strict=True, reason="bdf.battinfo._pin not implemented")
def test_the_stamped_schema_version_matches_the_semver_pattern() -> None:
    """The stamped schema version matches the pattern the bundled schemas
    enforce for `schema_version`."""
    with patch("requests.get", _mock_get()):
        pin = refresh(_REF)
    assert _SEMVER.match(pin["schema_version"])


@pytest.mark.xfail(strict=True, reason="bdf.battinfo._pin not implemented")
def test_an_update_refuses_an_unreadable_upstream_constant() -> None:
    """battinfo-bundle-pin: An update refuses an unreadable upstream constant.

    An absent or unparseable upstream constant fails the update, names the
    constant it could not read, and replaces no bundled file.
    """
    version_path = _bundled_version_path()
    before = version_path.read_text(encoding="utf-8")
    with (
        patch("requests.get", _mock_get(bundle_text="# no constant here\n")),
        pytest.raises(Exception, match="SCHEMA_VERSION"),
    ):
        refresh(_REF)
    assert version_path.read_text(encoding="utf-8") == before


@pytest.mark.xfail(strict=True, reason="bdf.battinfo._pin not implemented")
def test_a_commit_after_the_newest_tag_still_stamps_a_version() -> None:
    """battinfo-bundle-pin: A commit after the newest tag still stamps a
    version.

    The update requests no release tag, and the written pin declares no tag
    key.
    """
    mock_get = _mock_get()
    with patch("requests.get", mock_get):
        pin = refresh(_REF)
    for call in mock_get.call_args_list:
        url = call.args[0] if call.args else call.kwargs.get("url", "")
        assert "tag" not in str(url).lower()
        assert "release" not in str(url).lower()
    assert "tag" not in pin


def _bundled_version_path() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "bdf" / "data" / "battinfo" / "VERSION"
