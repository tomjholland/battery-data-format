"""Thin shim: resolves Source and delegates to Normalizer.normalize()."""

from __future__ import annotations

import polars as pl

from .schema import Normalizer, Source
from .sources import REGISTRY, get_normalizer


def _detect_source(headers: list[str]) -> Source | None:
    best: Source | None = None
    best_score = 0
    for n in REGISTRY:
        sc = n.score(headers)
        if sc > best_score:
            best = n
            best_score = sc
    return best


def normalize(
    df: pl.DataFrame | pl.LazyFrame,
    source: str | Source | None = None,
    *,
    include_optional: bool = True,
    column_map: dict[str, str] | None = None,
    extra_columns: dict[str, str] | None = None,
) -> pl.DataFrame | pl.LazyFrame:
    """Map vendor columns to BDF canonical names with unit conversion and dtype casting."""
    schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
    headers = list(schema.names())

    src: Source | None
    if isinstance(source, Source):
        src = source
    elif isinstance(source, str):
        src = get_normalizer(source)
    else:
        src = _detect_source(headers)

    if src is None and not column_map and not extra_columns:
        return df

    normalizer: Normalizer = src.normalizer if src is not None else Normalizer()
    return normalizer.normalize(
        df,
        include_optional=include_optional,
        column_map=column_map,
        extra_columns=extra_columns,
    )
