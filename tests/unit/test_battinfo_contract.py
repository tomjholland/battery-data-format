from __future__ import annotations

import ast
import inspect
import json

import pydantic

import bdf.metadata
from bdf.metadata import Metadata


def test_no_runtime_battinfo_import() -> None:
    """BDF must never import battinfo at runtime, keeping the
    battinfo[processing] -> batterydf dependency direction acyclic: the
    module source carries no import naming it, and a fresh record's
    ``to_dict()`` serialisation carries no trace of it either."""
    tree = ast.parse(inspect.getsource(bdf.metadata))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "battinfo" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or node.module.split(".")[0] != "battinfo"

    dumped = Metadata().to_dict()
    assert isinstance(dumped, dict)
    assert "battinfo" not in json.dumps(dumped)


def test_read_metadata_exposes_no_importer_adapter() -> None:
    """The serialised ``Metadata`` is the handoff contract itself: the
    public methods BDF adds are ``to_dict()`` and ``check()`` — no
    ``to_battinfo`` or other adapter producing importer keyword arguments."""

    def _public_methods(cls: type) -> set[str]:
        return {name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name))}

    own = _public_methods(Metadata) - _public_methods(pydantic.BaseModel)
    # check() is the only method this change adds; anything more would be a second contract to keep.
    assert own == {"check", "to_dict"}
