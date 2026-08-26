"""A schema-bounded record over a plain dictionary.

A :class:`Record` gives attribute access over a backing dictionary, bounded
by one resolved schema node. A field whose node declares ``properties`` or
``patternProperties`` reads back as a nested :class:`Record`, and any other
field reads back as the stored value. The backing dictionary is the
serialised form, so serialisation prunes empty values rather than converting
a model tree.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import urljoin

import jsonschema
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from bdf.battinfo.schema import BattinfoSchema, SchemaError

_OBJECT_KEYWORDS = ("properties", "patternProperties")
"""The keywords that make a field a nested record rather than a stored value."""


def _absolute_refs(node: Any, base_id: str) -> Any:
    """Copy a schema node, with every reference resolved against one document.

    A reference reads against the document that declares it. A node validated
    on its own sits outside that document, so a document-relative reference is
    written out in full first.

    Args:
        node: The schema node, list, or leaf value to copy.
        base_id: The ``$id`` the node's references resolve against.

    Returns:
        The copy, carrying absolute references alone.
    """
    if isinstance(node, dict):
        return {
            key: urljoin(base_id, value) if key == "$ref" and isinstance(value, str) else _absolute_refs(value, base_id)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_absolute_refs(item, base_id) for item in node]
    return node


def _is_object(node: dict[str, Any]) -> bool:
    """Report whether a resolved node declares fields of its own.

    Args:
        node: The resolved schema node to read.

    Returns:
        ``True`` where the node declares ``properties`` or
        ``patternProperties``.
    """
    return any(keyword in node for keyword in _OBJECT_KEYWORDS)


def _is_empty(value: Any) -> bool:
    """Report whether a value carries nothing, so serialisation drops it.

    A zero and a ``False`` carry a value, so neither is empty.

    Args:
        value: The stored value to test.

    Returns:
        ``True`` where the value is ``None`` or an empty mapping, list, or
        string.
    """
    return value is None or (isinstance(value, (dict, list, str)) and len(value) == 0)


def _prune(value: Any) -> Any:
    """Copy a value, without an empty entry at any depth.

    Args:
        value: The stored value to copy.

    Returns:
        The copy, holding the entries that carry a value.
    """
    if isinstance(value, dict):
        pruned = {key: _prune(item) for key, item in value.items()}
        return {key: item for key, item in pruned.items() if not _is_empty(item)}
    if isinstance(value, list):
        items = [_prune(item) for item in value]
        return [item for item in items if not _is_empty(item)]
    return copy.deepcopy(value)


class Record:
    """A schema-bounded view over one node of a backing dictionary.

    Args:
        schema: The loaded schema set this record is bounded by.
        node: The resolved schema node this record is bounded by.
        base_id: The ``$id`` ``node`` resolved against.
        data: The backing dictionary this record reads and writes.
        path: The dotted field path from the record root to this record.
    """

    def __init__(
        self,
        schema: BattinfoSchema,
        node: dict[str, Any],
        base_id: str,
        data: dict[str, Any],
        path: str,
    ) -> None:
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_base_id", base_id)
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name: str) -> Any:
        """Read a declared field, or a named entry under a key pattern.

        Args:
            name: The field, or entry name, to read.

        Returns:
            A nested :class:`Record` where the field's node declares
            ``properties`` or ``patternProperties``, the stored value where it
            does not, or ``None`` where nothing was set.

        Raises:
            AttributeError: The node declares no such field, and it declares
                no key pattern.
            SchemaError: The node declares a key pattern, and the name matches
                none of them.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        node, base_id = self._field(name)
        if not _is_object(node):
            return self._data.get(name)
        value = self._data.setdefault(name, {})
        if not isinstance(value, dict):
            return value
        return Record(self._schema, node, base_id, value, self._field_path(name))

    def __setattr__(self, name: str, value: Any) -> None:
        """Validate ``value`` against the field's own node, then store it.

        Args:
            name: The field, or entry name, to write.
            value: The value to validate and store.

        Raises:
            AttributeError: The node declares no such field, and it declares
                no key pattern.
            SchemaError: The node declares a key pattern and the name matches
                none of them, or the field's node refuses the value.
        """
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        node, base_id = self._field(name)
        refusals = self._refusals(name, node, base_id, value)
        if refusals:
            raise SchemaError(refusals[0])
        self._data[name] = value

    def __eq__(self, other: object) -> bool:
        """Report whether ``other`` is a record over equal data.

        Args:
            other: The value to compare against.

        Returns:
            ``True`` where ``other`` is a :class:`Record` whose serialised
            data equals this record's serialised data.
        """
        if not isinstance(other, Record):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    __hash__ = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dict, with every empty value pruned.

        Returns:
            A deep copy of the backing dictionary, pruned of every value that
            was never set.
        """
        pruned: dict[str, Any] = _prune(self._data)
        return pruned

    def validate(self) -> list[str]:
        """Validate every set value against its own node.

        Returns:
            One string per refused value, each naming a field path and a
            reason. An empty list means the record holds no refused value.
        """
        errors: list[str] = []
        for name, value in self._data.items():
            try:
                node, base_id = self._field(name)
            except (AttributeError, SchemaError) as exc:
                errors.append(str(exc))
                continue
            if _is_object(node) and isinstance(value, dict):
                errors.extend(Record(self._schema, node, base_id, value, self._field_path(name)).validate())
            else:
                errors.extend(self._refusals(name, node, base_id, value))
        return errors

    def _field(self, name: str) -> tuple[dict[str, Any], str]:
        """Resolve one field's node, through a declared field or a key pattern.

        Args:
            name: The field, or entry name, to resolve.

        Returns:
            The resolved node, and the ``$id`` it resolved against.

        Raises:
            AttributeError: The node declares no such field, and it declares
                no key pattern.
            SchemaError: The node declares a key pattern, and the name matches
                none of them.
        """
        declared = self._node.get("properties", {})
        if name in declared:
            return self._schema.resolve(declared[name], self._base_id)
        patterns = self._node.get("patternProperties", {})
        for pattern, node in patterns.items():
            if re.search(pattern, name):
                return self._schema.resolve(node, self._base_id)
        field_path = self._field_path(name)
        if patterns:
            raise SchemaError(
                f"BattINFO schema {self._version()} declares no key pattern matching {field_path}: "
                f"the accepted patterns are {', '.join(sorted(patterns))}"
            )
        raise AttributeError(f"BattINFO schema {self._version()} declares no field {field_path}")

    def _refusals(self, name: str, node: dict[str, Any], base_id: str, value: Any) -> list[str]:
        """Report what the field's node refuses about a value.

        Args:
            name: The field, or entry name, the value is stored under.
            node: The field's resolved node.
            base_id: The ``$id`` ``node`` resolved against.
            value: The value to validate.

        Returns:
            One string per refusal, each naming the field path, the reason,
            and the loaded schema version. An empty list means the node
            accepts the value.
        """
        field_path = self._field_path(name)
        validator = jsonschema.Draft202012Validator(
            _absolute_refs(node, base_id),
            registry=self._schema._registry,
        )
        reasons = [error.message for error in validator.iter_errors(value)]
        return [f"BattINFO schema {self._version()} refuses {field_path}: {reason}" for reason in reasons]

    def _field_path(self, name: str) -> str:
        """Return the dotted path from the record root to one field.

        Args:
            name: The field, or entry name, to name.

        Returns:
            The field's path, which is its name alone at the record root.
        """
        return f"{self._path}.{name}" if self._path else name

    def _version(self) -> str:
        """Return the loaded schema version, for a message that names it.

        Returns:
            The version the loaded set reports, or ``unversioned``.
        """
        return self._schema.version or "unversioned"

    @classmethod
    def _serialise(cls, value: Any) -> Any:
        """Serialise a record, or pass another value through.

        Args:
            value: The value pydantic is serialising.

        Returns:
            What :meth:`to_dict` returns for a record, and ``value`` itself
            for anything else.
        """
        return value.to_dict() if isinstance(value, Record) else value

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler, /) -> CoreSchema:
        """Give pydantic a validator and a serialiser for this type.

        Args:
            source: The annotated type pydantic is building a schema for.
            handler: Callback that builds the schema for a nested type.

        Returns:
            A core schema that accepts a :class:`Record` or a mapping on
            validation, and that serialises through :meth:`to_dict`.
        """
        return core_schema.json_or_python_schema(
            json_schema=core_schema.dict_schema(),
            python_schema=core_schema.union_schema([core_schema.is_instance_schema(cls), core_schema.dict_schema()]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._serialise,
                return_schema=core_schema.any_schema(),
                when_used="always",
            ),
        )
