"""Helpers for BattINFO tests: release directories, a cache fixture, and a fake ``requests.get``."""

from __future__ import annotations

import json
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import requests

import bdf.battinfo
from bdf.battinfo import BATTINFO_SOURCE

# Read at import, before any test points the source at another directory.
BUNDLED = Path(str(files("bdf.data") / "battinfo"))

RAW_URL = "https://raw.githubusercontent.com/BIG-MAP/BattINFO/{tag}/assets/schemas/{path}"


def bundled_docs() -> dict[str, dict[str, Any]]:
    """Return the documents of the packaged release, keyed by their path under ``schemas/``."""
    root = BUNDLED / "schemas"
    return {p.relative_to(root).as_posix(): json.loads(p.read_text()) for p in sorted(root.rglob("*.schema.json"))}


def release_docs(field: str) -> dict[str, dict[str, Any]]:
    """Return a release whose ``test`` record is closed and holds one integer ``field``."""
    record = {"type": "object", "additionalProperties": False, "properties": {field: {"type": "integer"}}}
    doc = {"$id": "https://example.test/test.schema.json", "properties": {"test": record}}
    return {"test.schema.json": doc}


def write_release(root: Path, version: str, docs: dict[str, dict[str, Any]]) -> Path:
    """Write the release directory ``root`` (``VERSION`` and ``schemas/``) from ``docs``."""
    root.mkdir(parents=True)
    (root / "VERSION").write_text(f"{version}\n")
    for path, doc in docs.items():
        (root / "schemas" / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "schemas" / path).write_text(json.dumps(doc))
    return root


CACHE_DIR = "cache"


@pytest.fixture
def battinfo_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use an empty cache, ``tmp_path / CACHE_DIR``, and forget the source's remembered failures around the test."""
    monkeypatch.setenv("BDF_CACHE_DIR", str(tmp_path / CACHE_DIR))
    BATTINFO_SOURCE.forget()
    yield tmp_path / CACHE_DIR
    BATTINFO_SOURCE.forget()


def cache_release(tmp_path: Path, version: str, docs: dict[str, dict[str, Any]]) -> None:
    """Put a release in the cache that ``battinfo_cache`` uses."""
    write_release(tmp_path / CACHE_DIR / f"battinfo-v{version}", version, docs)


def point_bundle_at(monkeypatch: pytest.MonkeyPatch, root: Path, version: str, docs: dict[str, dict[str, Any]]) -> None:
    """Make ``docs`` the bundled release: the source's bundle and ``BATTINFO_SCHEMA``."""
    monkeypatch.setattr(BATTINFO_SOURCE, "bundled", write_release(root, version, docs))
    monkeypatch.setattr(bdf.battinfo, "BATTINFO_SCHEMA", BATTINFO_SOURCE.get(version, fetch=False))


class FakeRawGitHub:
    """Stands in for requests.get: serves the schema documents of releases by tag and path."""

    def __init__(self, docs: dict[str, dict[str, Any]] | None = None, tag: str = "v0.9.0") -> None:
        self.docs = docs if docs is not None else bundled_docs()
        self.tag = tag
        self.down = False
        self.urls: list[str] = []
        self.timeouts: list[float | None] = []

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.urls.append(url)
        self.timeouts.append(kwargs.get("timeout"))
        if self.down:
            raise requests.ConnectionError("down")
        for path, doc in self.docs.items():
            if url == RAW_URL.format(tag=self.tag, path=path):
                return FakeResponse(200, json.dumps(doc))
        return FakeResponse(404, "")


class FakeResponse:
    """The parts of a ``requests.Response`` that a document fetch reads."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self.content = text.encode()

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))
