"""BattINFO entity data as plain dicts, checked by jsonschema against a whole document.

An entity's data is the dict the sidecar serialises, and nothing wraps it.
Every check validates the whole entity against its document's root schema
(:meth:`BattinfoSchema.root_validator`), so jsonschema itself decides
``anyOf``/``oneOf``, ``$ref`` across documents, and ``additionalProperties``.

A value is coerced before it is checked, the way pydantic's lax mode coerces:
where jsonschema reports a string where it expected an ``integer``,
``number``, or ``boolean``, the string is converted where pydantic converts it
(``"25"`` to ``25.0`` for a number) and the entity is checked again. A
boolean is never converted to a number.
"""

from __future__ import annotations

import copy
from typing import Any, Iterator, Sequence

from pydantic import TypeAdapter, ValidationError
from referencing.exceptions import Unresolvable

from bdf.battinfo.schema import BattinfoSchema, SchemaError

_LAX_TYPES: dict[str, TypeAdapter[Any]] = {
    "integer": TypeAdapter(int),
    "number": TypeAdapter(float),
    "boolean": TypeAdapter(bool),
}
"""JSON Schema's scalar types a string is coerced to, each through pydantic's lax mode."""

_MAX_COERCION_PASSES = 10
"""How many times coercion converts and checks again before it stops."""


def _is_empty(value: Any) -> bool:
    """Report whether a value carries nothing, so pruning drops it.

    A zero and a ``False`` carry a value, so neither is empty.

    Args:
        value: The value to test.

    Returns:
        ``True`` where the value is ``None`` or an empty mapping, list, or
        string.
    """
    return value is None or (isinstance(value, (dict, list, str)) and len(value) == 0)


def has_value(value: Any) -> bool:
    """Report whether a value holds anything pruning keeps, without copying it.

    Args:
        value: The value to test.

    Returns:
        ``True`` where :func:`prune` leaves the value not empty.
    """
    if isinstance(value, dict):
        return any(has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(has_value(item) for item in value)
    return not _is_empty(value)


def prune(value: Any) -> Any:
    """Copy a value, without an empty entry at any depth, so a stated null reads as unset.

    Args:
        value: The value to copy.

    Returns:
        The copy, holding the entries that carry a value.
    """
    if isinstance(value, dict):
        pruned = {key: prune(item) for key, item in value.items()}
        return {key: item for key, item in pruned.items() if not _is_empty(item)}
    if isinstance(value, list):
        items = [prune(item) for item in value]
        return [item for item in items if not _is_empty(item)]
    return copy.deepcopy(value)


def refusals(schema: BattinfoSchema, doc_name: str, data: dict[str, Any]) -> list[Any]:
    """Return every error the document's root schema reports for an entity's data.

    Args:
        schema: The schema set to check against.
        doc_name: The bundle-relative document the entity follows.
        data: The entity's data.

    Returns:
        The ``jsonschema`` errors, each locating its value by ``absolute_path``.

    Raises:
        SchemaError: A reference names no document in the set, or the
            references loop without consuming the value.
    """
    try:
        return list(schema.root_validator(doc_name).iter_errors(data))
    except Unresolvable as exc:
        raise SchemaError(f"BattINFO schema {schema.label} reference {exc.ref} names no document in the set") from exc
    except RecursionError as exc:
        raise SchemaError(f"BattINFO schema {schema.label} references in {doc_name} loop without end") from exc


def _type_errors(errors: Sequence[Any]) -> Iterator[Any]:
    """Yield each ``type`` error, at any depth of a combinator's sub-errors.

    Args:
        errors: The errors to search.

    Yields:
        Each error whose failed keyword is ``type``.
    """
    for error in errors:
        if error.validator == "type":
            yield error
        yield from _type_errors(error.context or ())


def _converted(value: Any, expected: Any) -> Any:
    """Convert a string to the one scalar type a ``type`` keyword expects.

    Args:
        value: The refused value.
        expected: The ``type`` keyword's value, a name or a list of names.

    Returns:
        The converted value, or ``value`` itself where no single
        ``integer``, ``number``, or ``boolean`` is expected (``null`` aside),
        or pydantic does not convert it.
    """
    names = [expected] if isinstance(expected, str) else list(expected)
    names = [name for name in names if name != "null"]
    if not isinstance(value, str) or len(names) != 1 or names[0] not in _LAX_TYPES:
        return value
    try:
        return _LAX_TYPES[names[0]].validate_python(value)
    except ValidationError:
        return value


def coerce(schema: BattinfoSchema, doc_name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Convert each string the schema refuses for its type, where lax mode converts it.

    Args:
        schema: The schema set to check against.
        doc_name: The bundle-relative document the entity follows.
        data: The entity's data. It is not modified.

    Returns:
        A copy of the data, every convertible string converted.
    """
    coerced = copy.deepcopy(data)
    for _ in range(_MAX_COERCION_PASSES):
        changed = False
        for error in _type_errors(refusals(schema, doc_name, coerced)):
            path = list(error.absolute_path)
            value = _converted(error.instance, error.validator_value)
            if not path or value is error.instance:
                continue
            parent = coerced
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            changed = True
        if not changed:
            break
    return coerced


def concerns(error: Any, keys: Sequence[Any]) -> bool:
    """Report whether an error lies on, above, or below one path of the entity.

    An ``anyOf`` error is reported on the value that holds the choice, which
    lies above a field written inside it, so an error above the path counts.

    Args:
        error: A ``jsonschema`` error.
        keys: The path written, below the entity.

    Returns:
        ``True`` where one path is a prefix of the other.
    """
    path = list(error.absolute_path)
    depth = min(len(path), len(keys))
    return path[:depth] == list(keys[:depth])


def message(schema: BattinfoSchema, entity: str, error: Any) -> str:
    """Name a refused value, the reason, and the schema release.

    Args:
        schema: The schema set that refused the value.
        entity: The ``Metadata`` field the entity is held under.
        error: The ``jsonschema`` error.

    Returns:
        One line, such as ``BattINFO schema 0.8.0rc2 refuses battinfo_test.test.name: ...``.
    """
    where = ".".join(str(part) for part in (entity, *error.absolute_path))
    return f"BattINFO schema {schema.label} refuses {where}: {error.message}"
