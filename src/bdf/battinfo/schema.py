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
layout, to the bundle or to the cache :meth:`BattinfoSchema.load_version` and
:meth:`BattinfoSchema.load_latest` read. :meth:`BattinfoSchema.for_version` returns one release's set without
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

from packaging.version import InvalidVersion, Version
from referencing import Registry, Resource
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

_TAGS_URL = "https://api.github.com/repos/BIG-MAP/BattINFO/tags?per_page=100"
"""The upstream API URL that lists the tags a latest release is chosen from."""

FETCH_ENV = "BDF_BATTINFO_FETCH"
"""Environment variable that turns off fetching a release a sidecar names, when set to ``0``, ``false``, ``no``, or ``off``."""

READ_FETCH_TIMEOUT = 5.0
"""Seconds one document fetch may take when a read fetches the release a sidecar names."""

_SUBSCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
"""The keywords whose values map a name to a subschema, rather than a keyword."""


def normalise_release(version: str) -> str:
    """Return a release as the set names it: no surrounding space, and no leading ``v``.

    Args:
        version: The release as a caller or a sidecar states it, e.g.
            ``"v0.8.0rc2"``.

    Returns:
        The release, e.g. ``"0.8.0rc2"``.
    """
    return version.strip().removeprefix("v")


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
        self._root_validators: dict[str, Any] = {}

    def __getstate__(self) -> dict[str, Any]:
        """Return the state to pickle: everything but the reference registry.

        The registry holds functions a pickle cannot carry, and it is rebuilt
        from the documents alone.

        Returns:
            The instance's attributes, without ``_registry`` or the validator cache.
        """
        state = self.__dict__.copy()
        del state["_registry"]
        del state["_root_validators"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore a pickled set, and rebuild its reference registry.

        Args:
            state: The attributes :meth:`__getstate__` returned.
        """
        self.__dict__.update(state)
        self._registry = _registry_for(self.docs)
        self._root_validators = {}

    @property
    def label(self) -> str:
        """The loaded release, for a message that names it: ``unversioned`` for a set of no release."""
        return self.version or "unversioned"

    def root_validator(self, doc_name: str) -> Any:
        """Return a validator for one whole document, cached for the set.

        The document is validated as it is, so jsonschema resolves its
        references, across documents too, and decides its combinators.

        Args:
            doc_name: The bundle-relative document name, e.g.
                ``"test.schema.json"``.

        Returns:
            A ``jsonschema.Draft202012Validator`` over the document, reaching
            every document in the set.
        """
        cached = self._root_validators.get(doc_name)
        if cached is None:
            import jsonschema

            cached = jsonschema.Draft202012Validator(self.docs[doc_name], registry=self._registry)
            self._root_validators[doc_name] = cached
        return cached

    def _adopt(self, other: "BattinfoSchema") -> None:
        """Take another loaded set's documents and version in place.

        Args:
            other: The set to adopt. A caller holding this instance reads the
                adopted documents through it.
        """
        self.version = other.version
        self.docs = other.docs
        self._registry = other._registry
        self._root_validators = {}

    @classmethod
    def build(cls) -> "BattinfoSchema":
        """Load the bundled snapshot, with no network access, and return a new set.

        Returns:
            The bundled set, named by the release ``src/bdf/data/battinfo/VERSION``
            holds.
        """
        return cls.from_snapshot(bundled_snapshot_dir())

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
    def get_snapshot(cls, dest: Path | str | None = None, version: str | None = None, *, timeout: float = 30.0) -> Path:
        """Fetch one upstream BattINFO release and write it as a snapshot.

        Each document is written with sorted keys and a two-space indent, the
        form the ``pretty-format-json`` pre-commit hook keeps, so a release
        that only reorders keys changes no bundled byte. The snapshot is
        written to a temporary directory and moved into place, so a failed
        fetch leaves the destination as it was.

        Args:
            dest: The snapshot directory to write. Defaults to the bundled
                package-data directory.
            version: The release to fetch, e.g. ``"0.8.0rc2"``. A leading
                ``v``, as the upstream tag carries, is accepted. When None,
                the latest release (:func:`latest_release`) is fetched.
            timeout: Seconds one document fetch may take.

        Returns:
            The snapshot directory written to.

        Raises:
            requests.HTTPError: A fetch failed, as it does for a version
                upstream has not released.
            SchemaError: A fetched document does not parse, or upstream lists
                no release.
        """
        import requests

        version = latest_release(timeout=timeout) if version is None else normalise_release(version)
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
        version = normalise_release(version)
        cached = _release_cache_dir(version)
        if refresh or not (cached / "VERSION").is_file():
            self.get_snapshot(dest=cached, version=version)
            _RELEASES.pop(version, None)
            _UNAVAILABLE.pop(version, None)
        self._adopt(self.from_snapshot(cached))

    def load_latest(self, *, refresh: bool = False) -> None:
        """Load the latest BattINFO release, using the cache or fetching it, and adopt it in place.

        Without ``refresh``, the newest release in the ``BDF_CACHE_DIR`` cache
        is adopted where there is one. Otherwise the latest upstream release
        (:func:`latest_release`) is fetched into that cache.

        Args:
            refresh: If True, ask upstream for its latest release and fetch it,
                ignoring the cache.

        Raises:
            requests.HTTPError: The tag listing or a fetch failed.
            SchemaError: Upstream lists no release, or a document does not parse.
        """
        if not refresh:
            cached = _latest_cached_release()
            if cached is not None:
                self._adopt(self.from_snapshot(cached))
                return
        self.load_version(latest_release(), refresh=True)

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
        version = normalise_release(version)
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
                    cls.get_snapshot(dest=snapshot, version=version, timeout=timeout)
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


def _release_key(tag: str) -> Version | None:
    """Return the version one release tag names, or ``None`` where it names no release.

    Args:
        tag: The tag, with a leading ``v``, e.g. ``"v0.8.0rc2"``.

    Returns:
        The version, which orders a pre-release before its final release, or
        ``None`` where ``tag`` has no leading ``v``, is not a version, or
        names a development or local build.
    """
    if not tag.startswith("v"):
        return None
    try:
        version = Version(tag[1:])
    except InvalidVersion:
        return None
    return None if version.is_devrelease or version.local else version


def latest_release(timeout: float = 30.0) -> str:
    """Ask upstream for its latest BattINFO release.

    The latest release is the newest ``v``-prefixed release tag, a
    pre-release included, because a pre-release is the release a snapshot
    pins until its final release is tagged.

    Args:
        timeout: Seconds the tag listing may take.

    Returns:
        The release, with no leading ``v``, e.g. ``"0.8.0rc2"``.

    Raises:
        requests.HTTPError: The tag listing failed.
        SchemaError: Upstream lists no release tag.
    """
    import requests

    response = requests.get(_TAGS_URL, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    tags = [tag.get("name") for tag in response.json() if isinstance(tag, dict)]
    releases = [(key, tag) for tag in tags if isinstance(tag, str) and (key := _release_key(tag)) is not None]
    if not releases:
        raise SchemaError(f"BattINFO lists no release tag at {_TAGS_URL}")
    return normalise_release(max(releases)[1])


def _latest_cached_release() -> Path | None:
    """Return the snapshot directory of the newest release in the cache, or ``None``.

    Returns:
        The cached snapshot whose ``VERSION`` names the newest release, or
        ``None`` where the cache holds no release.
    """
    from bdf.fetch import cache_dir

    cached: list[tuple[Version, Path]] = []
    for path in cache_dir().glob("battinfo-v*"):
        version = _snapshot_version(path)
        key = _release_key(f"v{version}") if version else None
        if key is not None:
            cached.append((key, path))
    return max(cached)[1] if cached else None


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


def _update_snapshot_cli() -> None:
    """Entry point: fetch a BattINFO release and update the bundled snapshot.

    Usage: bdf-update-battinfo [VERSION]

    With VERSION (e.g. '0.8.0'), fetches that release. Without, fetches the
    latest release upstream lists.
    """
    import sys

    version = sys.argv[1] if len(sys.argv) > 1 else None
    path = BattinfoSchema.get_snapshot(version=version)
    print(f"BattINFO snapshot updated: {path} (release {_snapshot_version(path)})")


BATTINFO_SCHEMA: BattinfoSchema = BattinfoSchema.build()
"""The loaded schema set every record is bounded by, unless a caller substitutes another."""
