"""BattINFO schema sets: the documents of one release, and the check of metadata against them.

A release is a directory with a ``VERSION`` file and the JSON Schema
documents under ``schemas/``. bdf bundles one release (:data:`BATTINFO_SCHEMA`)
and loads others through :data:`BATTINFO_SOURCE`, from the cache or from the
BattINFO repository. A set never changes after it is built, and each way to
load a release returns a new set.
"""

from __future__ import annotations

import importlib.resources
import json
import posixpath
import re
import time
import warnings
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import jsonschema
import referencing.exceptions
import requests
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from bdf._definitions import DefinitionSource
from bdf._errors import BDFDefinitionError, BDFDefinitionWarning

if TYPE_CHECKING:
    from bdf.metadata import Metadata

FIELD_SCHEMAS: dict[str, str] = {
    "battinfo_test": "test.schema.json",
    "battinfo_cell": "cell-instance.schema.json",
    "battinfo_channel": "channel.schema.json",
    "battinfo_equipment": "equipment.schema.json",
    "battinfo_test_protocol": "test-protocol.schema.json",
    "battinfo_dataset": "dataset.schema.json",
}
"""Each ``battinfo_*`` field of ``Metadata``, and the path under ``schemas/`` of the schema that checks it."""

# The BattINFO commit that bdf 0.2.0 stamped as ``battinfo_ref``, and the
# release with the same schemas.
_REF_RELEASES: dict[str, str] = {"2ea8929074a7430b6e437065686cda881cfc23e7": "0.8.0rc2"}

READ_FETCH_TIMEOUT = 5.0
"""Seconds that a sidecar read gives the fetch of the release it states."""

_RAW_URL = "https://raw.githubusercontent.com/BIG-MAP/BattINFO/{tag}/assets/schemas/{path}"


def _required(
    validator: Any, required: Any, instance: Any, schema: Mapping[str, Any]
) -> Iterator[jsonschema.ValidationError]:
    """Check ``required`` except where it lists the fields of a record, so a partial record is valid."""
    if "properties" in schema:
        return
    yield from jsonschema.Draft202012Validator.VALIDATORS["required"](validator, required, instance, schema)


_Validator = jsonschema.validators.extend(jsonschema.Draft202012Validator, {"required": _required})


class BattinfoSchema:
    """The schema documents of one BattINFO release. A set never changes after it is built."""

    version: str
    docs: dict[str, dict[str, Any]]

    def __init__(self, docs: Mapping[str, dict[str, Any]], version: str) -> None:
        """Hold the documents of one release.

        Args:
            docs: Each document's path under ``schemas/`` (``/`` separators) and its parsed JSON.
            version: The release the documents belong to.

        Raises:
            ValueError: If a document has no string ``$id``.
        """
        self.version = version
        self.docs = dict(docs)
        # Without $schema, a $ref into another document keeps _Validator.
        self._contents = {path: {k: v for k, v in doc.items() if k != "$schema"} for path, doc in self.docs.items()}
        for path, doc in self._contents.items():
            if not isinstance(doc.get("$id"), str):
                raise ValueError(f"BattINFO document {path} has no string $id")
        self._registry: Registry = Registry().with_resources(
            (doc["$id"], DRAFT202012.create_resource(doc)) for doc in self._contents.values()
        )
        self._validators: dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"BattinfoSchema({self.version!r})"

    @classmethod
    def build(cls) -> BattinfoSchema:
        """Load the bundled release, with no network access.

        Raises:
            BDFDefinitionError: If the bundled release is missing or unreadable.
        """
        try:
            return BATTINFO_SOURCE.get(BATTINFO_SOURCE.get_bundled_version() or "", fetch=False)
        except BDFDefinitionError as exc:
            raise BDFDefinitionError(
                f"The bundled BattINFO release at {BATTINFO_SOURCE.bundled} is missing or unreadable: {exc}. "
                "Restore it from git or reinstall bdf; bdf-update-battinfo VERSION writes a release into it."
            ) from exc

    @classmethod
    def from_version(cls, version: str, *, refresh: bool = False) -> BattinfoSchema:
        """Load a release from the bundle, the cache, or upstream, as a new set.

        Args:
            version: The release name.
            refresh: If True, fetch the release again.

        Raises:
            BDFDefinitionError: If the release cannot be loaded.
        """
        return BATTINFO_SOURCE.get(version, refresh=refresh)

    def check(self, field: str, data: Mapping[str, Any]) -> list[str]:
        """Return one line for each value in ``data`` that the schema for ``field`` refuses.

        Args:
            field: A ``battinfo_*`` field of ``Metadata``, such as ``"battinfo_test"``.
            data: The value of that field.

        Raises:
            KeyError: If ``field`` is not a ``battinfo_*`` field.
            BDFDefinitionError: If ``data`` is not empty and the set has no schema for ``field``.
            referencing.exceptions.Unresolvable: If a ``$ref`` names no document.
            re.error: If a ``pattern`` does not compile.
            RecursionError: If references loop.
        """
        path = FIELD_SCHEMAS[field]
        if not data:
            return []
        validator = self._validators.get(field)
        if validator is None:
            if path not in self._contents:
                raise BDFDefinitionError(f"BattINFO {self.version} has no document {path} for {field}")
            validator = self._validators[field] = _Validator(self._contents[path], registry=self._registry)
        lines = []
        for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
            # A combinator's own message names no key: name its most relevant branch error.
            while error.context:
                error = min(error.context, key=jsonschema.exceptions.relevance)
            where = ".".join(str(p) for p in (field, *error.absolute_path))
            lines.append(f"BattINFO {self.version} refuses {where}: {error.message}")
        return lines


def _open_battinfo(path: Path) -> BattinfoSchema:
    """Open a release directory: the source's open hook.

    Raises:
        jsonschema.exceptions.SchemaError: If a document breaks the Draft 2020-12 metaschema.
    """
    root = path / "schemas"
    docs = {
        p.relative_to(root).as_posix(): json.loads(p.read_text(encoding="utf-8")) for p in root.rglob("*.schema.json")
    }
    for doc in docs.values():
        _Validator.check_schema(doc)
    return BattinfoSchema(docs, (path / "VERSION").read_text(encoding="utf-8").strip())


def _refs(node: Any) -> Iterator[str]:
    """Yield every ``$ref`` string in a document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def _download_battinfo(tag: str, dest: Path, timeout: float) -> None:
    """Fetch the six schemas in ``FIELD_SCHEMAS`` at ``tag``, and every document that their ``$ref`` reach.

    Raises:
        ValueError: If a ``$ref`` is a URL, is absolute, or leaves ``assets/schemas/``.
        TimeoutError: If ``timeout`` seconds pass before the last request.
    """
    deadline = time.monotonic() + timeout
    pending = list(FIELD_SCHEMAS.values())
    seen = set(pending)
    while pending:
        rel = pending.pop(0)
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"BattINFO {tag} did not arrive within {timeout} s")
        response = requests.get(_RAW_URL.format(tag=tag, path=rel), timeout=left)
        response.raise_for_status()
        doc = json.loads(response.content)
        out = dest / "schemas" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        for ref in _refs(doc):
            target = ref.partition("#")[0]
            if not target:
                continue
            joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
            if urlsplit(target).scheme or target.startswith("/") or joined.startswith(".."):
                raise ValueError(f"{rel} refers to {ref!r}, outside the release's schemas")
            if joined not in seen:
                seen.add(joined)
                pending.append(joined)


def _no_latest_battinfo(timeout: float) -> str:
    """Raise: BattINFO publishes no final release, so it has no latest release."""
    raise BDFDefinitionError(
        "BattINFO publishes no final release, so there is no latest release; "
        "name a version, for example bdf-update-battinfo 0.8.0rc2"
    )


BATTINFO_SOURCE: DefinitionSource[BattinfoSchema] = DefinitionSource(
    "battinfo",
    bundled=Path(str(importlib.resources.files("bdf.data") / "battinfo")),
    tag_prefix="v",
    find_latest=_no_latest_battinfo,
    download=_download_battinfo,
    open=_open_battinfo,
)

BATTINFO_SCHEMA: BattinfoSchema = BattinfoSchema.build()
"""The bundled release. Nothing in bdf rebinds or changes it after import."""

# The errors of a set that loads but cannot be used for the check.
_UNUSABLE = (referencing.exceptions.Unresolvable, re.error, RecursionError, BDFDefinitionError)


def _fall_back(stated: str, where: str | Path, reason: BaseException, strict: bool, stacklevel: int) -> None:
    """Warn that the bundle stands in for a stated release, or with ``strict`` raise."""
    bundled = BATTINFO_SCHEMA.version
    if strict:
        raise BDFDefinitionError(
            f"Could not use BattINFO release {stated} that {where}'s metadata states: {reason}. "
            f"Connect or cache the release, pass battinfo=, or pass validate=False to check "
            f"against the bundled release {bundled} and keep release {stated}."
        ) from reason
    warnings.warn(
        f"Could not use BattINFO release {stated} that {where}'s metadata states: {reason}. "
        f"Checking against the bundled release {bundled}.",
        BDFDefinitionWarning,
        stacklevel=stacklevel + 1,
    )


def _release_schema(
    stated: str | None, where: str | Path, *, strict: bool = False, stacklevel: int
) -> tuple[BattinfoSchema, bool]:
    """Return the schema set for the release that metadata states, or the bundle.

    A stated release that cannot be named or loaded gives the bundle, with a
    ``BDFDefinitionWarning``, and True as the second value. ``stacklevel``
    counts from this function's caller.

    Raises:
        BDFDefinitionError: If ``strict`` and the stated release cannot be loaded.
    """
    if not stated:
        return BATTINFO_SCHEMA, False
    try:
        name = BATTINFO_SOURCE.normalise(stated)
        if name == BATTINFO_SOURCE.normalise(BATTINFO_SCHEMA.version):
            return BATTINFO_SCHEMA, False
        return BATTINFO_SOURCE.get(name, timeout=READ_FETCH_TIMEOUT), False
    except BDFDefinitionError as exc:
        _fall_back(stated, where, exc, strict, stacklevel + 1)
        return BATTINFO_SCHEMA, True


def get_stated_release(metadata: Metadata) -> str | None:
    """Return the BattINFO release in ``metadata.bdf.battinfo_version``, or the release of a bdf 0.2.0 ``battinfo_ref``."""
    ref = metadata.bdf.__dict__["battinfo_ref"]  # not the attribute, which warns that it is deprecated
    return metadata.bdf.battinfo_version or _REF_RELEASES.get(ref or "")


def _refusals(schema: BattinfoSchema, metadata: Metadata) -> list[str]:
    return [line for field in FIELD_SCHEMAS for line in schema.check(field, getattr(metadata, field))]


def _check_metadata(
    metadata: Metadata,
    where: str | Path,
    *,
    schema: BattinfoSchema | None = None,
    strict: bool = False,
    stacklevel: int,
) -> tuple[list[str], BattinfoSchema | None, bool]:
    """Return the refusal lines of the six ``battinfo_*`` fields, and the set that gave them.

    Args:
        metadata: The metadata to check.
        where: The file or object the metadata belongs to, for a warning.
        schema: The set to check against. Without it: the release that the
            metadata states, else the bundle, falling back to the bundle with a
            ``BDFDefinitionWarning`` where the stated release cannot be used.
        strict: Raise ``BDFDefinitionError``, rather than fall back, where the
            stated release cannot be used.
        stacklevel: ``warnings.warn`` stack level, counted from the caller.

    Returns:
        ``([], None, False)`` where every ``battinfo_*`` field is empty; otherwise the lines,
        the set they were checked against, and whether that is the bundle in
        place of a stated release. A refused value never raises.
    """
    if not any(getattr(metadata, field) for field in FIELD_SCHEMAS):
        return [], None, False
    if schema is not None:
        return _refusals(schema, metadata), schema, False
    stated = get_stated_release(metadata)
    schema, fell_back = _release_schema(stated, where, strict=strict, stacklevel=stacklevel + 1)
    try:
        return _refusals(schema, metadata), schema, fell_back
    except _UNUSABLE as exc:
        if schema is BATTINFO_SCHEMA:
            raise
        _fall_back(str(stated), where, exc, strict, stacklevel + 1)
        return _refusals(BATTINFO_SCHEMA, metadata), BATTINFO_SCHEMA, True


def _update_snapshot_cli() -> None:
    """Run ``bdf-update-battinfo VERSION``, which writes a release into the bundle."""
    BATTINFO_SOURCE.run_cli("bdf-update-battinfo")
