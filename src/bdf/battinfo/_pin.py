"""Read and refresh the bundled BattINFO pin.

The pin stamps four values beside the bundled schema documents: the
upstream repository, the commit pinned, the schema version, and the
BattINFO library version. The two version values are read from upstream
source, by a literal parse of one constant each, with no import of the
upstream package.
"""

from __future__ import annotations

from typing import Mapping

BUNDLE_VERSION_PATH = "src/battinfo/bundle.py"
"""Upstream path holding the ``SCHEMA_VERSION`` constant."""

BATTINFO_VERSION_PATH = "src/battinfo/__init__.py"
"""Upstream path holding the ``__version__`` constant."""


def bundle_pin() -> Mapping[str, str]:
    """Read the bundled pin (``repo``, ``ref``, ``schema_version``, ``battinfo_version``).

    Returns:
        The bundled pin's keys and values. A missing or unparseable file
        returns an empty mapping.
    """
    raise NotImplementedError


def refresh(ref: str) -> dict[str, str]:
    """Fetch the upstream version constants at ``ref``, and stamp the pin.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit to read the constants at.

    Returns:
        The pin to write: ``repo``, ``ref``, ``schema_version``, and
        ``battinfo_version``.
    """
    raise NotImplementedError
