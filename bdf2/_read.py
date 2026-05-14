"""File reading: layout detection, CSV scan, and metadata extraction."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

import polars as pl

from ._config import load_config
from ._detect import detect_layout, read_sample, sniff_source
from ._normalize import normalize


def read(
    path: str | Path,
    source: str | None = None,
    lazy: bool = False,
) -> tuple[Union[pl.DataFrame, pl.LazyFrame], dict]:
    """
    Read a battery cycler file and return (bdf_df, metadata).

    Detects source via magic strings, infers separator and header position via
    run-length heuristic, reads all columns as strings, then normalises via
    normalize().  Preamble metadata is extracted using source metadata_patterns.
    """
    path = Path(path)
    config = load_config()

    sample = read_sample(path)
    head_bytes = path.read_bytes()[:8192]

    if source is None:
        source = sniff_source(head_bytes, config)

    sep, header_idx, _data_start, has_header = detect_layout(sample)
    preamble_lines = sample.splitlines()[:header_idx]

    lf = pl.scan_csv(
        path,
        skip_rows=header_idx,
        separator=sep,
        has_header=has_header,
        infer_schema=False,
    )

    bdf_lf, meta = normalize(lf, source=source)

    confirmed_source = meta.get("source")
    if confirmed_source:
        patterns = config["sources"].get(confirmed_source, {}).get("metadata_patterns", {})
        for key, pattern in patterns.items():
            rx = re.compile(pattern, re.IGNORECASE)
            for line in preamble_lines:
                m = rx.search(line)
                if m:
                    meta[key] = m.group(1).strip()
                    break

    if lazy:
        return bdf_lf, meta
    return bdf_lf.collect(), meta
