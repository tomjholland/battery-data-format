"""BattINFO record models.

The record models are generated from the bundled upstream schemas
(:mod:`bdf.battinfo.generated`).
"""

from __future__ import annotations

from bdf.battinfo._fetch import MANAGED_SCHEMA_PATHS
from bdf.battinfo._pin import bundle_pin
from bdf.battinfo.schema import BATTINFO_SCHEMA, BattinfoSchema, SchemaError

__all__ = ["BATTINFO_SCHEMA", "MANAGED_SCHEMA_PATHS", "BattinfoSchema", "SchemaError", "bundle_pin", "bundled_ref"]


def bundled_ref() -> str | None:
    """Return the upstream BattINFO commit the bundled schemas were fetched at.

    Reads the ``ref`` key of the bundled pin (see :func:`bundle_pin`).

    Returns:
        The ref string, or None when the stamp is missing or unreadable.
    """
    return bundle_pin().get("ref") or None
