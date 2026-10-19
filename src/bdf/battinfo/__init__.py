"""BattINFO records, bounded by a loaded schema set.

:data:`BATTINFO_SCHEMA` holds the schema set a record is bounded by, loaded
from the bundled documents, and adopts another set in place from a directory
(``BATTINFO_SCHEMA.load_dir``). :class:`Record` gives attribute access over
one node of it. :class:`SchemaError` names a value the loaded set refuses.
:func:`bundled_ref` names the upstream commit the bundle was fetched at.
"""

from __future__ import annotations

from bdf.battinfo.record import Record
from bdf.battinfo.schema import BATTINFO_SCHEMA, BattinfoSchema, SchemaError, bundled_ref

__all__ = [
    "BATTINFO_SCHEMA",
    "BattinfoSchema",
    "Record",
    "SchemaError",
    "bundled_ref",
]
