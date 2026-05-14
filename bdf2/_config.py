"""JSON config loader and synonym index construction."""

from __future__ import annotations

import json
import re
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "columns.json"

_config_cache: dict | None = None
_index_cache: dict[str, dict[str, tuple[str, str]]] | None = None


def load_config() -> dict:
    """Read and cache columns.json (singleton per process)."""
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            _config_cache = json.load(fh)
    return _config_cache


def _extract_quantity(text: str, regexes: list[re.Pattern]) -> str:
    """Apply regexes in order; on hit return lowercased group(1); else full lowercase string."""
    for rx in regexes:
        m = rx.match(text)
        if m:
            return m.group(1).lower()
    return text.lower()


def build_synonym_index(config: dict) -> dict[str, dict[str, tuple[str, str]]]:
    """Build {source_id: {quantity_str: (bdf_key, bdf_unit)}} from config."""
    index: dict[str, dict[str, tuple[str, str]]] = {}
    columns = config["columns"]

    for source_id, source_spec in config["sources"].items():
        regexes = [re.compile(r) for r in source_spec.get("qty_unit_regexes", [])]
        source_index: dict[str, tuple[str, str]] = {}

        for bdf_key, col_def in columns.items():
            bdf_unit = col_def["unit"]
            for synonym in col_def.get("synonyms", {}).get(source_id, []):
                qty = _extract_quantity(synonym, regexes)
                source_index[qty] = (bdf_key, bdf_unit)

        index[source_id] = source_index

    return index


def get_synonym_index() -> dict[str, dict[str, tuple[str, str]]]:
    """Return cached synonym index (built on first call)."""
    global _index_cache
    if _index_cache is None:
        _index_cache = build_synonym_index(load_config())
    return _index_cache
