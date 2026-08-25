"""A schema-bounded record over a plain dictionary.

A :class:`Record` gives attribute access over a backing dictionary, bounded
by one resolved schema node. A field whose node declares ``properties`` or
``patternProperties`` reads back as a nested :class:`Record`, and any other
field reads back as the stored value. The backing dictionary is the
serialised form, so serialisation prunes empty values rather than converting
a model tree.
"""

from __future__ import annotations

from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema

from bdf.battinfo.schema import BattinfoSchema


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
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        """Read a declared field, or a named entry under a key pattern.

        Args:
            name: The field, or entry name, to read.

        Returns:
            A nested :class:`Record` where the field's node declares
            ``properties`` or ``patternProperties``, the stored value where it
            does not, or ``None`` where nothing was set.
        """
        raise NotImplementedError

    def __setattr__(self, name: str, value: Any) -> None:
        """Validate ``value`` against the field's own node, then store it.

        Args:
            name: The field, or entry name, to write.
            value: The value to validate and store.
        """
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dict, with every empty value pruned.

        Returns:
            A deep copy of the backing dictionary, pruned of every value that
            was never set.
        """
        raise NotImplementedError

    def validate(self) -> list[str]:
        """Validate every set value against its own node.

        Returns:
            One string per refused value, each naming a field path and a
            reason. An empty list means the record holds no refused value.
        """
        raise NotImplementedError

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
        raise NotImplementedError
