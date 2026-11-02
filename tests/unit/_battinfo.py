"""Write BattINFO snapshots for the tests: the bundled documents, optionally edited."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from bdf.battinfo.schema import bundled_schema_dir, load_docs

Docs = dict[str, dict[str, Any]]


def declare_rig_bay(docs: Docs) -> None:
    """Declare a ``rig_bay`` string on a test, a field no BattINFO release declares.

    Args:
        docs: The loaded documents, edited in place.
    """
    docs["test.schema.json"]["properties"]["rig_bay"] = {"type": "string"}


def write_snapshot(dest: Path, version: str | None = None, edit: Callable[[Docs], None] | None = None) -> Path:
    """Copy the bundled documents into ``dest/schemas``, edit them, and name the release.

    Args:
        dest: The snapshot directory to write, in the layout
            ``BattinfoSchema.get_snapshot`` writes.
        version: The release ``VERSION`` names, or ``None`` to write no
            ``VERSION``.
        edit: A callable that edits the loaded documents in place.

    Returns:
        ``dest``.
    """
    schemas = dest / "schemas"
    shutil.copytree(bundled_schema_dir(), schemas)
    if edit is not None:
        docs = load_docs(schemas)
        edit(docs)
        for name, doc in docs.items():
            (schemas / name).write_text(json.dumps(doc), encoding="utf-8")
    if version is not None:
        (dest / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    return dest
