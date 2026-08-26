"""Fetch the managed BattINFO schema files at an upstream commit, and cache them.

A fetch accepts a 40-character commit alone. A commit is content-addressed,
so a cached set stays valid for ever and the cache needs no expiry. The cache
sits under :func:`bdf.fetch.cache_dir`, so it honours ``BDF_CACHE_DIR``. A
fetched set writes to a temporary directory and moves into place, so an
interrupted fetch leaves no partial set.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from bdf.battinfo._pin import RAW_URL_TEMPLATE
from bdf.battinfo.schema import BattinfoSchema, SchemaError, load_docs

MANAGED_SCHEMA_PATHS: dict[str, str] = {
    "test.schema.json": "assets/schemas/test.schema.json",
    "cell-instance.schema.json": "assets/schemas/cell-instance.schema.json",
    "cell-canonical.schema.json": "assets/schemas/cell-canonical.schema.json",
    "channel.schema.json": "assets/schemas/channel.schema.json",
    "equipment.schema.json": "assets/schemas/equipment.schema.json",
    "test-protocol.schema.json": "assets/schemas/test-protocol.schema.json",
    "dataset.schema.json": "assets/schemas/dataset.schema.json",
    "modules/common/quantitative-properties.schema.json": "assets/schemas/modules/common/quantitative-properties.schema.json",
    "modules/common/quantity.schema.json": "assets/schemas/modules/common/quantity.schema.json",
}
"""Bundle-relative path to upstream path, for each managed schema file."""

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
"""How large one fetched document can be before the fetch refuses it."""

_COMMIT = re.compile(r"[0-9a-f]{40}")
"""The commit a fetch accepts: 40 hexadecimal characters, and nothing else."""


def fetch_documents(ref: str) -> dict[str, bytes]:
    """Fetch the managed schema documents at ``ref``.

    Each document passes a size cap and a JSON parse, so a fetch carries the
    risk of wrong data alone.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit or tag to read.

    Returns:
        A mapping from each document's bundle-relative path to its raw bytes.

    Raises:
        requests.HTTPError: A fetch failed.
        SchemaError: A document exceeds the size cap, or does not parse.
    """
    import requests

    fetched: dict[str, bytes] = {}
    for rel_name, upstream_path in MANAGED_SCHEMA_PATHS.items():
        url = RAW_URL_TEMPLATE.format(ref=ref, path=upstream_path)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        content = response.content
        if len(content) > MAX_DOCUMENT_BYTES:
            raise SchemaError(
                f"BattINFO document {rel_name} from {url} is {len(content)} bytes, "
                f"over the {MAX_DOCUMENT_BYTES} byte cap"
            )
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"BattINFO document {rel_name} from {url} is not valid JSON: {exc}") from exc
        fetched[rel_name] = content
    return fetched


def cache_path(ref: str) -> Path:
    """Return the cache directory one commit's schema set is written to.

    Args:
        ref: The commit the set was fetched at.

    Returns:
        A directory under :func:`bdf.fetch.cache_dir`, named by the commit.
    """
    from bdf.fetch import cache_dir

    return cache_dir() / f"battinfo-{ref}"


def _from_ref(ref: str, refresh: bool = False) -> BattinfoSchema:
    """Fetch or read the cached schema set for a 40-character commit.

    The private transport :meth:`bdf.battinfo.schema.BattinfoSchema.from_ref`
    delegates to.

    Args:
        ref: A 40-character hexadecimal upstream ``BIG-MAP/BattINFO`` commit.
        refresh: Refetch even where a cached set already exists.

    Returns:
        The loaded set at ``ref``.

    Raises:
        ValueError: ``ref`` is not 40 hexadecimal characters. The fetch makes
            no request.
        requests.HTTPError: A fetch failed.
        SchemaError: A document exceeds the size cap, or does not parse.
    """
    if not _COMMIT.fullmatch(ref):
        raise ValueError(f"BattINFO schema set {ref!r} is not a 40-character commit, so no fetch can pin it")
    dest = cache_path(ref)
    if dest.is_dir() and not refresh:
        return BattinfoSchema(load_docs(dest), ref=ref)

    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f"{dest.name}."))
    try:
        for rel_name, content in fetch_documents(ref).items():
            document = staging / rel_name
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_bytes(content)
        if dest.is_dir():
            shutil.rmtree(dest)
        staging.replace(dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BattinfoSchema(load_docs(dest), ref=ref)
