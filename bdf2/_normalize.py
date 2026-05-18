"""Column mapping, unit conversion, and type coercion driven by Normalizer instances."""

from __future__ import annotations

import logging
import warnings

import polars as pl

from .schema import (
    BDFColumn,
    Normalizer,
    ResolvedColumn,
    Style,
    _norm_unit,
    _parse_styled,
    _pint_scale,
)
from .sources import REGISTRY, get_normalizer

_logger = logging.getLogger(__name__)
_MR_TO_COL: dict[str, BDFColumn] = {c.mr_name: c for c in BDFColumn}


def _sniff_decimal(df: pl.DataFrame | pl.LazyFrame) -> str:
    """Return ',' if comma-decimal strings dominate string columns, else '.'."""
    sample = df.head(1000).collect() if isinstance(df, pl.LazyFrame) else df.head(1000)

    comma = dot = 0
    for col in sample.columns:
        if sample[col].dtype in (pl.String, pl.Utf8):
            comma += sample[col].str.count_matches(r"\d+,\d+").sum()
            dot += sample[col].str.count_matches(r"\d+\.\d+").sum()

    return "," if comma > dot else "."


def _resolved_from_column_map_value(col: BDFColumn, src_header: str) -> ResolvedColumn:
    """Build a ResolvedColumn for a column_map override by parsing src_header for units."""
    src_unit: str | None = None
    for style, _qty, unit in _parse_styled(src_header):
        if style != Style.NONE and unit is not None:
            src_unit = unit
            break
    if src_unit is None:
        scale = 1.0
    else:
        factor = _pint_scale(src_unit, col.unit)
        if factor is None:
            warnings.warn(
                f"column_map: unit {src_unit!r} on {src_header!r} not compatible "
                f"with {col.unit!r} for {col.mr_name}; using scale=1.0",
                UserWarning,
                stacklevel=3,
            )
            scale = 1.0
        else:
            scale = factor
    return ResolvedColumn(
        source_header=src_header,
        bdf_unit=col.unit,
        scale=scale,
        offset=0.0,
        datetime_fmt=None,
    )


def _build_expr(
    col: BDFColumn,
    rc: ResolvedColumn,
    schema: dict,
    decimal: str,
) -> pl.Expr:
    """Construct the polars expression that produces the BDF column from rc.source_header."""
    src = rc.source_header
    if rc.datetime_fmt is not None:
        expr = (
            pl.col(src)
            .str.to_datetime(rc.datetime_fmt, strict=False)
            .dt.timestamp("ms")
            .cast(pl.Float64) / 1000.0
        )
        return expr.alias(col.mr_name)
    expr = pl.col(src)
    if decimal != "." and schema.get(src) in (pl.String, pl.Utf8):
        expr = expr.str.replace_all(decimal, ".", literal=True)
    expr = expr.cast(pl.Float64, strict=False)
    if rc.offset != 0.0:
        expr = expr + rc.offset
    if rc.scale != 1.0:
        expr = expr * rc.scale
    if col.dtype == "int":
        expr = expr.cast(pl.Int64, strict=False)
    return expr.alias(col.mr_name)


def _detect_source(headers: list[str]) -> Normalizer | None:
    best: Normalizer | None = None
    best_score = 0
    for n in REGISTRY:
        sc = n.score(headers)
        if sc > best_score:
            best = n
            best_score = sc
    return best


def normalize(
    df: pl.DataFrame | pl.LazyFrame,
    source: str | Normalizer | None = None,
    *,
    include_optional: bool = True,
    column_map: dict[str, str] | None = None,
    extra_columns: dict[str, str] | None = None,
    decimal: str | None = None,
) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
    """Map vendor columns to BDF canonical names with unit conversion and dtype casting.

    Returns ``(df_out, metadata)`` where ``metadata`` carries ``"source"`` (the id of
    the resolved Normalizer or ``None``) and ``"columns"`` (per-BDF-column provenance
    keyed by ``mr_name``).
    """
    if column_map:
        for k in column_map:
            if k not in _MR_TO_COL:
                raise ValueError(f"column_map key {k!r} is not a valid BDF mr_name")

    schema = df.collect_schema() if isinstance(df, pl.LazyFrame) else df.schema
    headers = list(schema.names())

    norm: Normalizer | None
    if isinstance(source, Normalizer):
        norm = source
    elif isinstance(source, str):
        norm = get_normalizer(source)
    else:
        norm = _detect_source(headers)

    metadata: dict = {"source": norm.id if norm is not None else None, "columns": {}}

    if norm is None and not column_map and not extra_columns:
        return df, metadata

    resolved: dict[BDFColumn, ResolvedColumn] = (
        norm.resolve(headers) if norm is not None else {}
    )

    if column_map:
        for mr_name, src_header in column_map.items():
            col = _MR_TO_COL[mr_name]
            resolved[col] = _resolved_from_column_map_value(col, src_header)

    if not include_optional:
        resolved = {c: r for c, r in resolved.items() if c.required}

    if decimal is None:
        decimal = _sniff_decimal(df)

    exprs: list[pl.Expr] = []
    seen_mr_names: set[str] = set()

    for col, rc in resolved.items():
        if col.mr_name in seen_mr_names:
            continue
        if not include_optional and not col.required:
            continue
        if rc.source_header not in headers:
            _logger.info(
                "normalize: column_map source %r not present in DataFrame; skipping",
                rc.source_header,
            )
            continue
        seen_mr_names.add(col.mr_name)
        src_unit: str | None = None
        if rc.datetime_fmt is None:
            for style, _qty, unit in _parse_styled(rc.source_header):
                if style != Style.NONE and unit is not None:
                    src_unit = _norm_unit(unit)
                    break
        metadata["columns"][col.mr_name] = {
            "source_header": rc.source_header,
            "source_unit": src_unit,
            "bdf_unit": rc.bdf_unit,
            "scale": rc.scale,
            "offset": rc.offset,
            "datetime_fmt": rc.datetime_fmt,
        }
        exprs.append(_build_expr(col, rc, dict(schema), decimal))

    if extra_columns:
        for src, out in extra_columns.items():
            if src not in headers:
                warnings.warn(
                    f"extra_columns source {src!r} not in DataFrame columns; skipping",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            exprs.append(pl.col(src).alias(out))

    if not exprs:
        return df, metadata

    df_out = df.select(exprs)
    return df_out, metadata
