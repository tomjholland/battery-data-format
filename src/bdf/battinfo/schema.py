"""Load, resolve, and substitute a BattINFO JSON Schema set.

A loaded set strips every ``required`` marker at load time, so a partial
record is never invalid, and a whole-record validation reports refused
values alone. :meth:`BattinfoSchema.resolve` follows a ``$ref`` chain and
combines ``allOf``, ``anyOf``, and ``oneOf`` into one node, so a caller reaches
a field through a plain attribute walk rather than through a reference chase.

The bundled set, under ``bdf/data/battinfo/``, is a snapshot of one upstream
BattINFO release, as the bundled ontology is a snapshot of one ontology
release: ``schemas/`` holds the documents, and ``VERSION`` names the release.
:meth:`BattinfoSchema.get_snapshot` fetches a release and writes it in that
layout, to the bundle or to the cache :meth:`BattinfoSchema.load_version`
reads. :meth:`BattinfoSchema.for_version` returns one release's set without
adopting it, so a sidecar is checked against the release its writer stamped
while :data:`BATTINFO_SCHEMA` stays as the caller left it.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

MANAGED_SCHEMAS = (
    "test.schema.json",
    "cell-instance.schema.json",
    "cell-canonical.schema.json",
    "channel.schema.json",
    "equipment.schema.json",
    "test-protocol.schema.json",
    "dataset.schema.json",
    "modules/common/quantitative-properties.schema.json",
    "modules/common/quantity.schema.json",
)
"""The schema documents a snapshot holds, by path under upstream ``assets/schemas/``."""

_RELEASE_URL_TMPL = "https://raw.githubusercontent.com/BIG-MAP/BattINFO/v{version}/assets/schemas/{path}"
"""The raw upstream URL of one schema document at one release tag."""

FETCH_ENV = "BDF_BATTINFO_FETCH"
"""Environment variable that turns off fetching a release a sidecar names, when set to ``0``, ``false``, ``no``, or ``off``."""

READ_FETCH_TIMEOUT = 5.0
"""Seconds one document fetch may take when a read fetches the release a sidecar names."""

MAX_REFERENCE_LINKS = 20
"""How many ``$ref`` links one resolution follows before it refuses the chain."""

_COMBINATORS = ("allOf", "anyOf", "oneOf")
"""The keywords whose subschemas combine into the node that declares them."""

_CHOICES = ("anyOf", "oneOf")
"""The combinators that hold one subschema at a time, rather than all of them."""

_UNIONED = ("properties", "patternProperties")
"""The keywords a combination unions across the subschemas it reads."""

_SUBSCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
"""The keywords whose values map a name to a subschema, rather than a keyword."""


class SchemaError(ValueError):
    """A reference, a document, or a value the loaded schema set refuses."""


def _strip_required(node: Any) -> Any:
    """Copy a schema node, without a ``required`` marker at any depth.

    A field named ``required`` survives, because a name under ``properties``
    is a field name and never a keyword.

    Args:
        node: The schema node, list, or leaf value to copy.

    Returns:
        The copy, with every ``required`` keyword removed.
    """
    if isinstance(node, dict):
        stripped: dict[str, Any] = {}
        for key, value in node.items():
            if key == "required":
                continue
            if key in _SUBSCHEMA_MAPS and isinstance(value, dict):
                stripped[key] = {name: _strip_required(sub) for name, sub in value.items()}
            else:
                stripped[key] = _strip_required(value)
        return stripped
    if isinstance(node, list):
        return [_strip_required(item) for item in node]
    return node


def load_docs(source: Path | str) -> dict[str, dict[str, Any]]:
    """Read every schema document under a directory.

    Args:
        source: Directory holding the schema documents. It is read
            recursively, so a document under ``modules/common`` is found.

    Returns:
        A mapping from each document's path relative to ``source``, with
        ``/`` separators, to its parsed content.

    Raises:
        SchemaError: The directory is missing, or a document does not parse.
    """
    root = Path(source)
    if not root.is_dir():
        raise SchemaError(f"BattINFO schema directory {root} does not exist")
    docs: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.schema.json")):
        try:
            docs[path.relative_to(root).as_posix()] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SchemaError(f"BattINFO schema document {path} does not parse: {exc}") from exc
    return docs


def _registry_for(docs: Mapping[str, dict[str, Any]]) -> Registry[Any]:
    """Build the reference registry over a set of documents, keyed by ``$id``.

    Args:
        docs: The documents, each declaring an ``$id``.

    Returns:
        A registry that resolves a reference to any of the documents.
    """
    return Registry().with_resources(
        (doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012)) for doc in docs.values()
    )


class BattinfoSchema:
    """A loaded BattINFO schema set, with every ``required`` marker stripped.

    Args:
        docs: Mapping from each document's bundle-relative file name (for
            example ``"cell-instance.schema.json"``) to its parsed JSON
            Schema content.
        version: The upstream BattINFO release the set is a snapshot of (for
            example ``"0.8.0rc2"``), or ``None`` for a set of no release.
    """

    version: str | None
    docs: dict[str, dict[str, Any]]

    def __init__(
        self,
        docs: Mapping[str, dict[str, Any]],
        version: str | None = None,
    ) -> None:
        self.version = version
        self.docs = {}
        for name, doc in docs.items():
            doc_id = doc.get("$id")
            if not isinstance(doc_id, str) or not doc_id:
                raise ValueError(f"BattINFO schema document {name} declares no $id")
            self.docs[name] = _strip_required(doc)
        self._registry: Registry[Any] = _registry_for(self.docs)
        self._roots: dict[str, tuple[dict[str, Any], str]] = {}

    def __getstate__(self) -> dict[str, Any]:
        """Return the state to pickle: everything but the reference registry.

        The registry holds functions a pickle cannot carry, and it is rebuilt
        from the documents alone.

        Returns:
            The instance's attributes, without ``_registry`` or the root cache.
        """
        state = self.__dict__.copy()
        del state["_registry"]
        del state["_roots"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore a pickled set, and rebuild its reference registry.

        Args:
            state: The attributes :meth:`__getstate__` returned.
        """
        self.__dict__.update(state)
        self._registry = _registry_for(self.docs)
        self._roots = {}

    def root(self, doc_name: str) -> tuple[dict[str, Any], str]:
        """Resolve one document's root node, cached for the set.

        Args:
            doc_name: The bundle-relative document name, e.g.
                ``"test.schema.json"``.

        Returns:
            The resolved root node, and the ``$id`` it resolved against.
        """
        cached = self._roots.get(doc_name)
        if cached is None:
            doc = self.docs[doc_name]
            cached = self.resolve(doc, doc["$id"])
            self._roots[doc_name] = cached
        return cached

    def resolve(self, node: dict[str, Any], base_id: str) -> tuple[dict[str, Any], str]:
        """Follow a ``$ref`` chain and combine the combinators into one node.

        Args:
            node: The schema node to resolve.
            base_id: The ``$id`` of the document ``node`` was read from.

        Returns:
            The resolved node, and the ``$id`` it resolved against.

        Raises:
            SchemaError: The chain runs over :data:`MAX_REFERENCE_LINKS`
                links, or a reference names no document in the set.
        """
        return self._resolve(node, base_id, [])

    def _resolve(self, node: dict[str, Any], base_id: str, chain: list[str]) -> tuple[dict[str, Any], str]:
        """Resolve one node, carrying the chain that reached it.

        Args:
            node: The schema node to resolve.
            base_id: The ``$id`` of the document ``node`` was read from.
            chain: The reference targets followed to reach ``node``.

        Returns:
            The resolved node, and the ``$id`` it resolved against.

        Raises:
            SchemaError: The chain runs over :data:`MAX_REFERENCE_LINKS`
                links, or a reference names no document in the set.
        """
        current: dict[str, Any] = _strip_required(node)
        while isinstance(current.get("$ref"), str):
            reference = current["$ref"]
            target = urljoin(base_id, reference)
            if len(chain) >= MAX_REFERENCE_LINKS:
                raise SchemaError(
                    f"BattINFO schema reference chain runs over {MAX_REFERENCE_LINKS} links: {' -> '.join(chain)}"
                )
            chain = [*chain, target]
            try:
                contents = self._registry.resolver(base_id).lookup(reference).contents
            except Unresolvable as exc:
                raise SchemaError(f"BattINFO schema reference {target} names no document in the loaded set") from exc
            if not isinstance(contents, dict):
                raise SchemaError(f"BattINFO schema reference {target} names no schema node")
            current = dict(contents)
            base_id = target.split("#", 1)[0] or base_id
        return self._combine(current, base_id, chain), base_id

    def _combine(self, node: dict[str, Any], base_id: str, chain: list[str]) -> dict[str, Any]:
        """Combine a node's ``allOf``, ``anyOf``, and ``oneOf`` subschemas into it.

        The combination serves field reach: it unions ``properties`` and
        ``patternProperties`` across the subschemas, so a caller reaches every
        declared field through one path.

        An ``allOf`` also takes the first value for every other keyword,
        because every subschema of an ``allOf`` holds at once. An ``anyOf`` or
        a ``oneOf`` keeps its own keyword on the node instead, so a validator
        still applies the choice, because one subschema alone holds.

        Args:
            node: The schema node to combine.
            base_id: The ``$id`` the node's references resolve against.
            chain: The reference targets followed to reach ``node``.

        Returns:
            One node, carrying the fields of every subschema and the choice
            keywords the node declares.
        """
        combined = {key: value for key, value in node.items() if key not in _COMBINATORS}
        for subschema in node.get("allOf", []):
            if not isinstance(subschema, dict):
                continue
            resolved, _ = self._resolve(subschema, base_id, chain)
            for key, value in resolved.items():
                if key in _UNIONED and isinstance(value, dict):
                    self._union(combined, key, value)
                elif key not in combined:
                    combined[key] = value
        for keyword in _CHOICES:
            subschemas = node.get(keyword)
            if not subschemas:
                continue
            combined[keyword] = subschemas
            for subschema in subschemas:
                if not isinstance(subschema, dict):
                    continue
                resolved, _ = self._resolve(subschema, base_id, chain)
                for key in _UNIONED:
                    value = resolved.get(key)
                    if isinstance(value, dict):
                        self._union(combined, key, value)
        return combined

    @staticmethod
    def _union(combined: dict[str, Any], key: str, value: dict[str, Any]) -> None:
        """Add a subschema's declared names under one keyword, keeping the first.

        Args:
            combined: The node being built, updated in place.
            key: The keyword to union under, ``properties`` or
                ``patternProperties``.
            value: The subschema's mapping of name to node.
        """
        union = dict(combined.get(key, {}))
        for name, subnode in value.items():
            union.setdefault(name, subnode)
        combined[key] = union

    def _adopt(self, other: "BattinfoSchema") -> None:
        """Take another loaded set's documents and version in place.

        Args:
            other: The set to adopt. A caller holding this instance reads the
                adopted documents through it.
        """
        self.version = other.version
        self.docs = other.docs
        self._registry = other._registry
        self._roots = {}

    @classmethod
    def from_snapshot(cls, path: Path | str) -> "BattinfoSchema":
        """Load a snapshot directory: the documents under ``schemas/``, and the release ``VERSION`` names.

        Args:
            path: The snapshot directory, as :meth:`get_snapshot` writes it.

        Returns:
            The loaded set.

        Raises:
            SchemaError: The directory is missing, or a document does not parse.
            ValueError: A document declares no ``$id``.
        """
        root = Path(path)
        return cls(load_docs(root / "schemas"), version=_snapshot_version(root))

    @classmethod
    def get_snapshot(cls, version: str, dest: Path | str | None = None, *, timeout: float = 30.0) -> Path:
        """Fetch one upstream BattINFO release and write it as a snapshot.

        Each document is written with sorted keys and a two-space indent, the
        form the ``pretty-format-json`` pre-commit hook keeps, so a release
        that only reorders keys changes no bundled byte. The snapshot is
        written to a temporary directory and moved into place, so a failed
        fetch leaves the destination as it was.

        Args:
            version: The release to fetch, e.g. ``"0.8.0rc2"``. A leading
                ``v``, as the upstream tag carries, is accepted.
            dest: The snapshot directory to write. Defaults to the bundled
                package-data directory.
            timeout: Seconds one document fetch may take.

        Returns:
            The snapshot directory written to.

        Raises:
            requests.HTTPError: A fetch failed, as it does for a version
                upstream has not released.
            SchemaError: A fetched document does not parse.
        """
        import requests

        version = version.removeprefix("v")
        dest = bundled_snapshot_dir() if dest is None else Path(dest)
        documents: dict[str, bytes] = {}
        for name in MANAGED_SCHEMAS:
            url = _RELEASE_URL_TMPL.format(version=version, path=name)
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            try:
                doc = json.loads(response.content)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"BattINFO document {name} from {url} is not valid JSON: {exc}") from exc
            documents[name] = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()

        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}."))
        try:
            for name, content in documents.items():
                (staging / "schemas" / name).parent.mkdir(parents=True, exist_ok=True)
                (staging / "schemas" / name).write_bytes(content)
            (staging / "VERSION").write_text(f"{version}\n", encoding="utf-8")
            if dest.is_dir():
                shutil.rmtree(dest)
            staging.replace(dest)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return dest

    def load_dir(self, source: Path | str) -> None:
        """Load a schema set from a directory, and adopt it in place.

        The adopted set names no release.

        Args:
            source: Directory holding the schema documents to adopt.

        Raises:
            SchemaError: The directory is missing, or a document does not parse.
            ValueError: A document declares no ``$id``.
        """
        self._adopt(BattinfoSchema(load_docs(source)))

    def load_version(self, version: str, *, refresh: bool = False) -> None:
        """Load one upstream BattINFO release, fetching it if not cached, and adopt it in place.

        A fetched release is cached under ``BDF_CACHE_DIR``, in the layout
        :meth:`get_snapshot` writes.

        Args:
            version: The release to load, e.g. ``"0.8.0rc2"``. A leading ``v``
                is accepted.
            refresh: If True, ignore the cache and re-fetch the release.

        Raises:
            requests.HTTPError: A fetch failed, as it does for a version
                upstream has not released.
            SchemaError: A document does not parse.
        """
        version = version.removeprefix("v")
        cached = _release_cache_dir(version)
        if refresh or not (cached / "VERSION").is_file():
            self.get_snapshot(version, dest=cached)
            _RELEASES.pop(version, None)
            _UNAVAILABLE.pop(version, None)
        self._adopt(self.from_snapshot(cached))

    @classmethod
    def for_version(cls, version: str, *, fetch: bool = True, timeout: float = 30.0) -> "BattinfoSchema":
        """Return one upstream BattINFO release's set, without adopting it.

        The bundled snapshot serves its own release with no network access,
        and the cache :meth:`load_version` writes serves any release fetched
        before. A release found nowhere is fetched into that cache. Each
        release loads once per process, and a release that failed to fetch
        is not fetched again, so a read of many files written at one
        unreachable release waits on one fetch alone.

        Args:
            version: The release, e.g. ``"0.8.0rc2"``. A leading ``v`` is
                accepted.
            fetch: If False, a release neither bundled nor cached is not
                fetched, and raises.
            timeout: Seconds one document fetch may take.

        Returns:
            The release's loaded set. It is shared by every caller asking for
            the release, so a caller must not substitute it in place.

        Raises:
            SchemaError: The release is neither bundled nor cached and
                ``fetch`` is False, it failed to fetch (now or earlier in the
                process), or a document does not parse.
            ValueError: A document declares no ``$id``.
        """
        version = version.removeprefix("v")
        loaded = _RELEASES.get(version)
        if loaded is not None:
            return loaded
        if version in _UNAVAILABLE:
            raise SchemaError(_UNAVAILABLE[version])
        snapshot = bundled_snapshot_dir()
        if _snapshot_version(snapshot) != version:
            snapshot = _release_cache_dir(version)
            if not (snapshot / "VERSION").is_file():
                if not fetch:
                    raise SchemaError(f"BattINFO release {version} is not cached, and fetching it is off")
                try:
                    cls.get_snapshot(version, dest=snapshot, timeout=timeout)
                # Whatever stops the fetch (no network, a socket a sandbox
                # blocks, a release upstream never published, a document
                # that does not parse), the release cannot be loaded.
                except Exception as exc:
                    reason = f"BattINFO release {version} could not be fetched: {exc}"
                    _UNAVAILABLE[version] = reason
                    raise SchemaError(reason) from exc
        loaded = cls.from_snapshot(snapshot)
        _RELEASES[version] = loaded
        return loaded


_RELEASES: dict[str, BattinfoSchema] = {}
"""The releases :meth:`BattinfoSchema.for_version` has loaded in this process, by release."""

_UNAVAILABLE: dict[str, str] = {}
"""The releases that failed to fetch in this process, each with the reason it failed."""


def _release_cache_dir(version: str) -> Path:
    """Return the cache directory one fetched release is written to.

    Args:
        version: The release, with no leading ``v``.

    Returns:
        The snapshot directory under ``BDF_CACHE_DIR``.
    """
    from bdf.fetch import cache_dir

    return cache_dir() / f"battinfo-v{version}"


def _snapshot_version(path: Path) -> str | None:
    """Return the release a snapshot directory's ``VERSION`` names.

    Args:
        path: The snapshot directory.

    Returns:
        The release, or ``None`` where the directory names none.
    """
    try:
        return (path / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def fetch_enabled() -> bool:
    """Report whether a read may fetch the BattINFO release a sidecar names.

    Returns:
        ``False`` where :data:`FETCH_ENV` is set to ``0``, ``false``, ``no``,
        or ``off``, and ``True`` otherwise.
    """
    return os.getenv(FETCH_ENV, "").strip().lower() not in {"0", "false", "no", "off"}


def bundled_snapshot_dir() -> Path:
    """Return the package-data directory holding the bundled snapshot.

    Returns:
        The directory holding ``schemas/`` and the ``VERSION`` file.
    """
    return Path(str(importlib.resources.files("bdf.data").joinpath("battinfo")))


def bundled_schema_dir() -> Path:
    """Return the package-data directory holding the bundled schema documents.

    Returns:
        The ``schemas/`` directory of :func:`bundled_snapshot_dir`.
    """
    return bundled_snapshot_dir() / "schemas"


def load_bundled() -> BattinfoSchema:
    """Load the bundled BattINFO schema set, with no network access.

    Returns:
        The bundled set, named by the release ``src/bdf/data/battinfo/VERSION``
        holds.
    """
    return BattinfoSchema.from_snapshot(bundled_snapshot_dir())


def _update_snapshot_cli() -> None:
    """Entry point: fetch a BattINFO release and update the bundled snapshot.

    Usage: bdf-update-battinfo VERSION
    """
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: bdf-update-battinfo VERSION (an upstream BattINFO release, e.g. 0.8.0)")
    path = BattinfoSchema.get_snapshot(sys.argv[1])
    print(f"BattINFO snapshot updated: {path} (release {sys.argv[1].removeprefix('v')})")


BATTINFO_SCHEMA: BattinfoSchema = load_bundled()
"""The loaded schema set every record is bounded by, unless a caller substitutes another."""
