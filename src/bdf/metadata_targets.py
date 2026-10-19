"""Where a metadata rule stages its value: the target types and the ``METADATA`` namespace.

A target is a frozen reference to one path in the document a read returns
(:class:`bdf.metadata.Metadata`). :data:`METADATA` mirrors that document as an
attribute path: ``METADATA.battinfo_test.test.started_at`` is a reference to
that field, and ``METADATA.extras["rig_bay"]`` is a reference to an ``extras``
key. Attribute access resolves against the loaded BattINFO schema
(:data:`bdf.battinfo.schema.BATTINFO_SCHEMA`) itself, so the schema stays the
single source of truth and no second representation of the same tree can go
stale.

An open map (e.g. ``test.conditions``) accepts a subscript instead of a
further attribute. A misspelled attribute raises ``AttributeError`` before any
parser is built. There is no target for an array-typed field, nor for ``bdf``,
which a read stamps itself.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from bdf.battinfo.schema import BATTINFO_SCHEMA
from bdf.metadata import _ENTITY_DOCS, _resolve_root


class MetadataTarget(BaseModel):
    """A frozen reference to one canonical path in the read metadata document.

    Constructed by the ``METADATA`` namespace, never directly; a rule
    pairs one target with its extraction and normalization, and the staging
    engine calls :meth:`stage` to place the rule's normalized value.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["metadata"] = "metadata"
    path: tuple[str, ...]

    def stage(self, document: dict, value: Any) -> None:
        """Set ``value`` at ``self.path`` in ``document``, creating intermediate dicts.

        Args:
            document: The staged metadata document under construction.
            value: The rule's normalized value to place at ``self.path``.
        """
        node = document
        for segment in self.path[:-1]:
            node = node.setdefault(segment, {})
        node[self.path[-1]] = value


class ExtrasTarget(BaseModel):
    """A frozen reference to one key under the staged document's ``extras`` mapping.

    Constructed by the ``METADATA`` namespace's ``extras`` subscript,
    never directly; lets a rule stage a vendor value under a chosen ``extras``
    key instead of a canonical field.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["extras"] = "extras"
    path: tuple[str, ...]

    def stage(self, document: dict, value: Any) -> None:
        """Set ``value`` at ``self.path`` under ``document["extras"]``.

        Args:
            document: The staged metadata document under construction.
            value: The rule's normalized value to place under ``extras``.
        """
        node = document.setdefault("extras", {})
        for segment in self.path[:-1]:
            node = node.setdefault(segment, {})
        node[self.path[-1]] = value


class _OpenMapTarget:
    """A schema node that accepts a subscript, not a further attribute."""

    __slots__ = ("_path",)

    def __init__(self, path: tuple[str, ...]) -> None:
        self._path = path

    def __getitem__(self, key: str) -> MetadataTarget:
        """Return the target for ``key`` under this open map.

        Args:
            key: The map key to target.

        Returns:
            A frozen :class:`MetadataTarget` for ``self._path + (key,)``.
        """
        return MetadataTarget(path=(*self._path, key))


class _ExtrasNamespace:
    """``METADATA.extras``: a subscript onto any key of the staged document's ``extras`` mapping."""

    __slots__ = ()

    def __getitem__(self, key: str) -> ExtrasTarget:
        """Return the extras target for ``key``.

        Args:
            key: The ``extras`` key to target.

        Returns:
            A frozen :class:`ExtrasTarget` for ``(key,)``.
        """
        return ExtrasTarget(path=(key,))


class _MetadataNode:
    """One node of the metadata target namespace, backed by a resolved schema node."""

    __slots__ = ("_path", "_node", "_base_id")

    def __init__(self, path: tuple[str, ...], node: dict[str, Any], base_id: str) -> None:
        self._path = path
        self._node = node
        self._base_id = base_id

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` against this node's declared fields.

        Args:
            name: The attribute segment to resolve.

        Returns:
            A :class:`MetadataTarget`, an :class:`_OpenMapTarget`, or a
            nested :class:`_MetadataNode`.

        Raises:
            AttributeError: ``name`` names no field of this node, or names an
                array-typed field, which is never a rule target.
        """
        path = (*self._path, name)
        declared = self._node.get("properties", {})
        if name not in declared:
            raise AttributeError(f"{'.'.join(path)!r} is not a declared BattINFO metadata path")

        # One segment per access, so a self-referencing schema costs nothing
        # until a caller walks into it.
        resolved, base_id = BATTINFO_SCHEMA.resolve(declared[name], self._base_id)
        if resolved.get("type") == "array":
            raise AttributeError(f"{'.'.join(path)!r} is an array field, which states no metadata target")
        if "properties" in resolved:
            return _MetadataNode(path, resolved, base_id)
        if "patternProperties" in resolved:
            return _OpenMapTarget(path)
        return MetadataTarget(path=path)


class _MetadataRoot:
    """The top-level ``METADATA`` namespace: the five entity records, plus ``extras``."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` against the entity records and ``extras``.

        Args:
            name: The attribute segment to resolve.

        Returns:
            An :class:`_ExtrasNamespace` for ``"extras"``, otherwise the
            :class:`_MetadataNode` for that entity record's root node.

        Raises:
            AttributeError: ``name`` names no entity record.
        """
        if name == "extras":
            return _ExtrasNamespace()
        doc_name = _ENTITY_DOCS.get(name)
        if doc_name is None:
            raise AttributeError(f"{name!r} is not a declared BattINFO metadata record")
        node, base_id = _resolve_root(doc_name)
        return _MetadataNode((name,), node, base_id)


METADATA = _MetadataRoot()
