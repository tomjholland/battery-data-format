"""JSON config loader and synonym index construction."""

from __future__ import annotations

import json
import re
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "columns.json"

_config_cache: dict | None = None
_index_cache: dict[str, dict[str, tuple[str, str]]] | None = None
_regex_cache: dict[str, list[re.Pattern]] | None = None
_datetime_index_cache: dict[str, dict[str, tuple[str, str, str]]] | None = None


def load_config() -> dict:
    """Read and cache columns.json (singleton per process)."""
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            _config_cache = json.load(fh)
    return _config_cache


def extract_qty_unit(header: str, regexes: list[re.Pattern]) -> tuple[str, str | None]:
    """Apply regexes in order; return (quantity, unit) or (full_header, None)."""
    for rx in regexes:
        m = rx.match(header)
        if m:
            return m.group(1), m.group(2)
    return header, None


def get_source_regexes() -> dict[str, list[re.Pattern]]:
    """Return cached compiled regexes per source (built on first call)."""
    global _regex_cache
    if _regex_cache is None:
        config = load_config()
        _regex_cache = {
            source_id: [re.compile(r) for r in spec.get("qty_unit_regexes", [])]
            for source_id, spec in config["sources"].items()
        }
    return _regex_cache


def build_synonym_index(config: dict) -> tuple[
    dict[str, dict[str, tuple[str, str]]],
    dict[str, dict[str, tuple[str, str, str]]],
]:
    """Build synonym and datetime indexes from config.

    Returns:
        (synonym_index, datetime_index) where:
        - synonym_index: {source_id: {qty_str: (bdf_key, bdf_unit)}}
        - datetime_index: {source_id: {header_lower: (bdf_key, bdf_unit, format_str)}}
    """
    synonym_index: dict[str, dict[str, tuple[str, str]]] = {}
    datetime_index: dict[str, dict[str, tuple[str, str, str]]] = {}
    columns = config["columns"]

    source_regexes = get_source_regexes()
    for source_id in config["sources"]:
        regexes = source_regexes.get(source_id, [])
        source_synonyms: dict[str, tuple[str, str]] = {}
        source_datetimes: dict[str, tuple[str, str, str]] = {}

        for bdf_key, col_def in columns.items():
            bdf_unit = col_def["unit"]
            for synonym in col_def.get("synonyms", {}).get(source_id, []):
                qty = extract_qty_unit(synonym, regexes)[0].lower()
                source_synonyms[qty] = (bdf_key, bdf_unit)

            dt_synonyms = col_def.get("datetime_synonyms", {}).get(source_id, {})
            for header, fmt in dt_synonyms.items():
                source_datetimes[header.lower()] = (bdf_key, bdf_unit, fmt)

        synonym_index[source_id] = source_synonyms
        datetime_index[source_id] = source_datetimes

    return synonym_index, datetime_index


def get_synonym_index() -> dict[str, dict[str, tuple[str, str]]]:
    """Return cached synonym index (built on first call)."""
    global _index_cache, _datetime_index_cache
    if _index_cache is None:
        _index_cache, _datetime_index_cache = build_synonym_index(load_config())
    return _index_cache


def get_datetime_index() -> dict[str, dict[str, tuple[str, str, str]]]:
    """Return cached datetime synonym index (built on first call)."""
    global _index_cache, _datetime_index_cache
    if _datetime_index_cache is None:
        _index_cache, _datetime_index_cache = build_synonym_index(load_config())
    return _datetime_index_cache
