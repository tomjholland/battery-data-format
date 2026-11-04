"""BattINFO record models.

The record models are generated from the bundled upstream schemas
(:mod:`bdf.battinfo.generated`). The runtime schema set checks entity dicts
against a BattINFO release.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bdf._definitions import DefinitionSource

if TYPE_CHECKING:
    from bdf.metadata import Metadata

ENTITY_DOCS: dict[str, str] = {
    "battinfo_test": "test.schema.json",
    "battinfo_cell": "cell-instance.schema.json",
    "battinfo_channel": "channel.schema.json",
    "battinfo_equipment": "equipment.schema.json",
    "battinfo_test_protocol": "test-protocol.schema.json",
    "battinfo_dataset": "dataset.schema.json",
}
"""Each Metadata entity field and the path of its document under ``schemas/``."""

# The BattINFO commit that bdf 0.2.0 stamped as ``battinfo_ref``, and the
# release with the same schemas.
_REF_RELEASES: dict[str, str] = {"2ea8929074a7430b6e437065686cda881cfc23e7": "0.8.0rc2"}

READ_FETCH_TIMEOUT = 5.0
"""Seconds that a sidecar read gives the fetch of the release it states."""


class BattinfoSchema:
    """A set of BattINFO schema documents of one release. It never changes after it is built."""

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
        raise NotImplementedError

    @classmethod
    def build(cls) -> BattinfoSchema:
        """Load the bundled release, with no network access.

        Raises:
            BDFDefinitionError: If the bundled release is missing or unreadable.
        """
        raise NotImplementedError

    @classmethod
    def from_version(cls, version: str, *, refresh: bool = False) -> BattinfoSchema:
        """Load a release from the bundle, the cache, or upstream, as a new set.

        Args:
            version: The release name.
            refresh: If True, fetch the release again.

        Raises:
            BDFDefinitionError: If the release cannot be loaded.
        """
        raise NotImplementedError

    def refusals(self, entity: str, data: Mapping[str, Any]) -> list[str]:
        """Return one line for each value of ``data`` that the entity's document refuses.

        Args:
            entity: A key of ``ENTITY_DOCS``.
            data: The entity's dict.

        Raises:
            KeyError: If ``entity`` is not in ``ENTITY_DOCS``.
            BDFDefinitionError: If ``data`` is not empty and the set has no document for ``entity``.
        """
        raise NotImplementedError


def _open_battinfo(path: Path) -> BattinfoSchema:
    """Open a release directory: the ``source``'s open hook."""
    raise NotImplementedError


def _download_battinfo(tag: str, dest: Path, timeout: float) -> None:
    """Fetch the six entity documents at ``tag`` and every document that their ``$ref`` reach."""
    raise NotImplementedError


def _no_latest_battinfo(timeout: float) -> str:
    """Raise: BattINFO publishes no final release, so it has no latest release."""
    raise NotImplementedError


BATTINFO_SOURCE: DefinitionSource[BattinfoSchema] = DefinitionSource(
    "battinfo",
    bundled=Path(str(importlib.resources.files("bdf.data") / "battinfo")),
    tag_prefix="v",
    find_latest=_no_latest_battinfo,
    download=_download_battinfo,
    open=_open_battinfo,
)

# The bundled release.
BATTINFO_SCHEMA: BattinfoSchema = BattinfoSchema.__new__(BattinfoSchema)


def _release_schema(stated: str | None, where: str | Path, *, stacklevel: int) -> BattinfoSchema:
    """Return the schema set for the release that metadata states, or the bundle."""
    raise NotImplementedError


def _check_metadata(
    metadata: Metadata, where: str | Path, *, schema: BattinfoSchema | None = None, stacklevel: int
) -> tuple[list[str], BattinfoSchema | None]:
    """Return the refusal lines of the six entities, and the set that gave them."""
    raise NotImplementedError


def _update_snapshot_cli() -> None:
    """Run ``bdf-update-battinfo [VERSION]``."""
    raise NotImplementedError


def bundled_ref() -> str | None:
    """Return the upstream BattINFO commit the bundled schemas were fetched at.

    Reads the ``ref`` line of ``bdf/data/battinfo/VERSION``. Upstream publishes
    no schema version numbers yet, so the ref is a commit hash; when BattINFO
    starts cutting versioned schema releases, this becomes a release tag.

    Returns:
        The ref string, or None when the stamp is missing or unreadable.
    """
    import importlib.resources

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
