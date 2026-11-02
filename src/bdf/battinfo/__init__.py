"""BattINFO entity data, checked against a loaded schema set.

:data:`BATTINFO_SCHEMA` holds the schema set entity data is checked against,
loaded from the bundled snapshot of one upstream release, and adopts another
set in place, as ``COLUMN_ONTOLOGY`` does: the latest upstream release
(``BATTINFO_SCHEMA.load_latest``), a named one (``load_version``), or a
directory (``load_dir``). ``BattinfoSchema.build()`` returns a fresh copy of
the bundled set.
:mod:`bdf.battinfo.document` checks and coerces an entity's plain dict.
:class:`SchemaError` names a value the loaded set refuses.
"""

from __future__ import annotations

from bdf.battinfo.schema import BATTINFO_SCHEMA, BattinfoSchema, SchemaError

__all__ = [
    "BATTINFO_SCHEMA",
    "BattinfoSchema",
    "SchemaError",
]
