"""BattINFO records, bounded by a loaded schema set.

:data:`BATTINFO_SCHEMA` holds the schema set a record is bounded by, loaded
from the bundled documents. :class:`BattinfoSchema` loads another set, from a
directory or from an upstream commit, and :class:`Record` gives attribute
access over one node of it. :class:`SchemaError` names a value the loaded set
refuses. :func:`bundle_pin` reads the bundled pin, and
:data:`MANAGED_SCHEMA_PATHS` names the documents a fetch reads.
"""

from __future__ import annotations

from bdf.battinfo._fetch import MANAGED_SCHEMA_PATHS
from bdf.battinfo._pin import bundle_pin
from bdf.battinfo.record import Record
from bdf.battinfo.schema import BATTINFO_SCHEMA, BattinfoSchema, SchemaError

__all__ = ["BATTINFO_SCHEMA", "MANAGED_SCHEMA_PATHS", "BattinfoSchema", "Record", "SchemaError", "bundle_pin", "bundled_ref"]


def bundled_ref() -> str | None:
    """Return the upstream BattINFO commit the bundled schemas were fetched at.

    Reads the ``ref`` key of the bundled pin (see :func:`bundle_pin`).

    Returns:
        The ref string, or None when the stamp is missing or unreadable.
    """
    return bundle_pin().get("ref") or None
