"""BattINFO records, bounded by a loaded schema set.

:data:`BATTINFO_SCHEMA` holds the schema set a record is bounded by, loaded
from the bundled snapshot of one upstream release, and adopts another set in
place, as ``COLUMN_ONTOLOGY`` does: an upstream release
(``BATTINFO_SCHEMA.load_version``) or a directory (``load_dir``).
:class:`Record` gives attribute access over one node of it.
:class:`SchemaError` names a value the loaded set refuses.
"""

from __future__ import annotations

from bdf.battinfo.record import Record
from bdf.battinfo.schema import BATTINFO_SCHEMA, BattinfoSchema, SchemaError

__all__ = [
    "BATTINFO_SCHEMA",
    "BattinfoSchema",
    "Record",
    "SchemaError",
]
