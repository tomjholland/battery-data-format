"""Load, resolve, and substitute a BattINFO JSON Schema set.

A loaded set strips every ``required`` marker at load time, so a partial
record is never invalid, and a whole-record validation reports refused
values alone. :meth:`BattinfoSchema.resolve` follows a ``$ref`` chain and
combines ``allOf``, ``anyOf``, and ``oneOf`` into one node, so a caller reaches
a field through a plain attribute walk rather than through a reference chase.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

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
        version: The upstream BattINFO version the set is a snapshot of, or
            ``None`` for a set of no version.
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

    def __getstate__(self) -> dict[str, Any]:
        """Return the state to pickle: everything but the reference registry.

        The registry holds functions a pickle cannot carry, and it is rebuilt
        from the documents alone.

        Returns:
            The instance's attributes, without ``_registry``.
        """
        state = self.__dict__.copy()
        del state["_registry"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore a pickled set, and rebuild its reference registry.

        Args:
            state: The attributes :meth:`__getstate__` returned.
        """
        self.__dict__.update(state)
        self._registry = _registry_for(self.docs)

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

    def load_dir(self, source: Path | str) -> None:
        """Load a schema set from a directory, and adopt it in place.

        The adopted set names no version.

        Args:
            source: Directory holding the schema documents to adopt.

        Raises:
            SchemaError: The directory is missing, or a document does not parse.
            ValueError: A document declares no ``$id``.
        """
        self._adopt(BattinfoSchema(load_docs(source)))


def bundled_schema_dir() -> Path:
    """Return the package-data directory holding the bundled schema documents.

    Returns:
        The directory ``load_bundled`` reads.
    """
    return Path(str(importlib.resources.files("bdf.data").joinpath("battinfo").joinpath("schemas")))


def bundled_ref() -> str | None:
    """Return the upstream BattINFO commit the bundled schemas were fetched at.

    Reads the ``ref`` line of ``bdf/data/battinfo/VERSION``. Upstream publishes
    no schema version numbers yet, so the ref is a commit hash; when BattINFO
    starts cutting versioned schema releases, this becomes a release tag.

    Returns:
        The ref string, or None when the stamp is missing or unreadable.
    """
    try:
        text = (
            importlib.resources.files("bdf.data").joinpath("battinfo").joinpath("VERSION").read_text(encoding="utf-8")
        )
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("ref="):
            return line.removeprefix("ref=").strip() or None
    return None


def load_bundled() -> BattinfoSchema:
    """Load the bundled BattINFO schema set, with no network access.

    Returns:
        The bundled set, named by the commit ``src/bdf/data/battinfo/VERSION``
        stamps.
    """
    return BattinfoSchema(load_docs(bundled_schema_dir()), version=bundled_ref())


BATTINFO_SCHEMA: BattinfoSchema = load_bundled()
"""The loaded schema set every record is bounded by, unless a caller substitutes another."""
