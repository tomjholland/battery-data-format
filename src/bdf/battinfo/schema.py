"""Load, resolve, and substitute a BattINFO JSON Schema set.

A loaded set strips every ``required`` marker at load time, so a partial
record is never invalid, and a whole-record validation reports refused
values alone. :meth:`BattinfoSchema.resolve` follows a ``$ref`` chain and
flattens ``allOf``, ``anyOf``, and ``oneOf`` into one node, so a caller reaches
a field through a plain attribute walk rather than through a reference chase.
"""

from __future__ import annotations

from typing import Any, Mapping


class SchemaError(ValueError):
    """A reference, a document, or a value the loaded schema set refuses."""


class BattinfoSchema:
    """A loaded BattINFO schema set, with every ``required`` marker stripped.

    Args:
        docs: Mapping from each document's bundle-relative file name (for
            example ``"cell-instance.schema.json"``) to its parsed JSON
            Schema content.
        version: The schema version the loaded set reports, or ``None``.
        ref: The upstream commit the loaded set was fetched at, or ``None``.
    """

    version: str | None
    ref: str | None

    def __init__(
        self,
        docs: Mapping[str, dict[str, Any]],
        version: str | None = None,
        ref: str | None = None,
    ) -> None:
        raise NotImplementedError

    def resolve(self, node: dict[str, Any], base_id: str) -> tuple[dict[str, Any], str]:
        """Follow a ``$ref`` chain and flatten combinators into one node.

        Args:
            node: The schema node to resolve.
            base_id: The ``$id`` of the document ``node`` was read from.

        Returns:
            The resolved node, and the ``$id`` it resolved against.
        """
        raise NotImplementedError

    @classmethod
    def from_ref(cls, ref: str, refresh: bool = False) -> "BattinfoSchema":
        """Fetch or read the cached schema set for a 40-character commit.

        Args:
            ref: A 40-character hexadecimal upstream ``BIG-MAP/BattINFO`` commit.
            refresh: Refetch even where a cached set already exists.

        Returns:
            The loaded set at ``ref``.
        """
        raise NotImplementedError


def load_bundled() -> BattinfoSchema:
    """Load the bundled BattINFO schema set, with no network access.

    Returns:
        The bundled set, pinned to the version and the commit
        ``src/bdf/data/battinfo/VERSION`` stamps.
    """
    raise NotImplementedError
