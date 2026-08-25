"""Fetch the managed BattINFO schema files at an upstream commit, and cache them.

A fetch accepts a 40-character commit alone. A commit is content-addressed,
so a cached set stays valid for ever and the cache needs no expiry. The cache
sits under :func:`bdf.fetch.cache_dir`, so it honours ``BDF_CACHE_DIR``. A
fetched set writes to a temporary directory and moves into place, so an
interrupted fetch leaves no partial set.
"""

from __future__ import annotations

from bdf.battinfo.schema import BattinfoSchema

MANAGED_SCHEMA_PATHS: dict[str, str] = {}
"""Bundle-relative path to upstream path, for each managed schema file."""


def _from_ref(ref: str, refresh: bool = False) -> BattinfoSchema:
    """Fetch or read the cached schema set for a 40-character commit.

    The private transport :meth:`bdf.battinfo.schema.BattinfoSchema.from_ref`
    delegates to.

    Args:
        ref: A 40-character hexadecimal upstream ``BIG-MAP/BattINFO`` commit.
        refresh: Refetch even where a cached set already exists.

    Returns:
        The loaded set at ``ref``.
    """
    raise NotImplementedError
