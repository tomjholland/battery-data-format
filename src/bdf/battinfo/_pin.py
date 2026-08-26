"""Read and refresh the bundled BattINFO pin.

The pin stamps four values beside the bundled schema documents: the
upstream repository, the commit pinned, the schema version, and the
BattINFO library version. The two version values are read from upstream
source, by a literal parse of one constant each, with no import of the
upstream package.
"""

from __future__ import annotations

import ast
import importlib.resources
from typing import Mapping

REPO = "BIG-MAP/BattINFO"
"""Upstream repository the bundled documents and constants come from."""

RAW_URL_TEMPLATE = f"https://raw.githubusercontent.com/{REPO}/{{ref}}/{{path}}"
"""The raw upstream URL one file is read from, by ref and path."""

BUNDLE_VERSION_PATH = "src/battinfo/bundle.py"
"""Upstream path holding the ``SCHEMA_VERSION`` constant."""

BATTINFO_VERSION_PATH = "src/battinfo/__init__.py"
"""Upstream path holding the ``__version__`` constant."""

_CONSTANTS: dict[str, tuple[str, str]] = {
    "schema_version": ("SCHEMA_VERSION", BUNDLE_VERSION_PATH),
    "battinfo_version": ("__version__", BATTINFO_VERSION_PATH),
}
"""Each pin key, with the upstream constant and the file that declares it."""


def bundle_pin() -> Mapping[str, str]:
    """Read the bundled pin (``repo``, ``ref``, ``schema_version``, ``battinfo_version``).

    Returns:
        The bundled pin's keys and values. A missing or unparseable file
        returns an empty mapping.
    """
    try:
        stamp = importlib.resources.files("bdf.data").joinpath("battinfo").joinpath("VERSION")
        text = stamp.read_text(encoding="utf-8")
    except Exception:
        return {}
    return {
        key.strip(): value.strip()
        for key, _, value in (line.partition("=") for line in text.splitlines())
        if key.strip() and value
    }


def _read_constant(text: str, name: str, path: str) -> str:
    """Read one string constant from upstream source, by a literal parse.

    Args:
        text: The upstream file's source.
        name: The constant to read, e.g. ``"SCHEMA_VERSION"``.
        path: The upstream path the source came from, for the message.

    Returns:
        The constant's string value.

    Raises:
        RuntimeError: The source does not parse, the constant is absent, or
            its value is not a string literal.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise RuntimeError(f"Upstream {path} does not parse, so {name} is unreadable: {exc}") from exc
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets: list[ast.expr] = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        raise RuntimeError(f"Upstream {path} declares {name}, but its value is not a string literal")
    raise RuntimeError(f"Upstream {path} declares no {name} constant")


def refresh(ref: str) -> dict[str, str]:
    """Fetch the upstream version constants at ``ref``, and stamp the pin.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit to read the constants at.

    Returns:
        The pin to write: ``repo``, ``ref``, ``schema_version``, and
        ``battinfo_version``.

    Raises:
        requests.HTTPError: A fetch failed.
        RuntimeError: A constant is absent or unparseable at ``ref``.
    """
    import requests

    pin = {"repo": REPO, "ref": ref}
    for key, (name, path) in _CONSTANTS.items():
        response = requests.get(RAW_URL_TEMPLATE.format(ref=ref, path=path), timeout=30)
        response.raise_for_status()
        pin[key] = _read_constant(response.text, name, path)
    return pin
