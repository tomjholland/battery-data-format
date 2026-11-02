"""Where a metadata rule stages its value: the target types and the ``METADATA`` namespace.

A target is a frozen reference to one path in the document a read returns
(:class:`bdf.metadata.Metadata`). :data:`METADATA` spells that path as
attributes and subscripts: ``METADATA.battinfo_test.test.started_at`` is a
reference to that field, ``METADATA.battinfo_test.test.conditions["temperature_c"]``
to an open map's entry, and ``METADATA.extras["rig_bay"]`` to an ``extras``
key.

A target reads no schema: the path is checked when a rule stages a value
there, by the check of the whole entity (:mod:`bdf.battinfo.document`). Only
the entity name is checked when the target is spelled. There is no target
for ``bdf``, which a read stamps itself.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from bdf.metadata import _ENTITY_DOCS


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

    def __getattr__(self, name: str) -> Any:
        """Extend the path by one field.

        Args:
            name: The field to append.

        Returns:
            The target one field below this one.
        """
        if name.startswith("_"):
            return super().__getattr__(name)  # type: ignore[misc]
        return MetadataTarget(path=(*self.path, name))

    def __getitem__(self, key: str) -> "MetadataTarget":
        """Extend the path by one key of an open map.

        Args:
            key: The map key to append.

        Returns:
            The target one key below this one.
        """
        return MetadataTarget(path=(*self.path, key))


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


class _MetadataRoot:
    """The top-level ``METADATA`` namespace: the five entity records, plus ``extras``."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` against the entity records and ``extras``.

        Args:
            name: The attribute segment to resolve.

        Returns:
            An :class:`_ExtrasNamespace` for ``"extras"``, otherwise the
            :class:`MetadataTarget` for that entity.

        Raises:
            AttributeError: ``name`` names no entity record.
        """
        if name == "extras":
            return _ExtrasNamespace()
        if name not in _ENTITY_DOCS:
            raise AttributeError(f"{name!r} is not a declared BattINFO metadata record")
        return MetadataTarget(path=(name,))


METADATA = _MetadataRoot()
