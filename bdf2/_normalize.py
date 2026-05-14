"""Column mapping, unit conversion, and type coercion."""

from __future__ import annotations

import re
import warnings
from typing import Union

import polars as pl
import pint

from ._config import get_synonym_index, load_config

_ureg = pint.UnitRegistry()

_COMMA_DEC = re.compile(r'\d+,\d+')
_DOT_DEC = re.compile(r'\d+\.\d+')

_UNIT_ALIASES: dict[str, str] = {
    "ma.h": "mAh",
    "w.h": "Wh",
    "mw.h": "mWh",
    "kw.h": "kWh",
    "°c": "degC",
    "\xb0c": "degC",
    "\xf8c": "degC",
    "sec": "s",
    "min": "min",
}


def _coerce_unit(u: str) -> str:
    return _UNIT_ALIASES.get(u.lower(), u)


def _sniff_decimal(df: Union[pl.DataFrame, pl.LazyFrame]) -> str:
    """Return ',' if comma-decimal strings dominate string columns, else '.'."""
    if isinstance(df, pl.LazyFrame):
        sample = df.head(1000).collect()
    else:
        sample = df.head(1000)

    comma = dot = 0
    for col in sample.columns:
        if sample[col].dtype in (pl.String, pl.Utf8):
            for val in sample[col]:
                if val is not None:
                    comma += len(_COMMA_DEC.findall(val))
                    dot += len(_DOT_DEC.findall(val))

    return "," if comma > dot else "."


def extract_qty_unit(header: str, regexes: list[re.Pattern]) -> tuple[str, str | None]:
    """Apply regexes in order; return (quantity, unit) or (full_header, None)."""
    for rx in regexes:
        m = rx.match(header)
        if m:
            return m.group(1), m.group(2)
    return header, None


def pint_factor(source_unit: str | None, bdf_unit: str) -> float | None:
    """
    Scalar conversion factor from source_unit to bdf_unit.
    Returns None if units are incompatible or unparseable.
    """
    if source_unit is None or source_unit.strip() == "" or bdf_unit in ("1", ""):
        return None
    src_str = _coerce_unit(source_unit.strip())
    tgt_str = _coerce_unit(bdf_unit.strip())
    if src_str.lower() == tgt_str.lower():
        return 1.0
    try:
        src = _ureg.parse_expression(src_str)
        tgt = _ureg.parse_expression(tgt_str)
        factor = float((src / tgt).to_base_units().magnitude)
        return round(factor, 15)
    except Exception as exc:
        warnings.warn(f"unit conversion failed ({source_unit!r} → {bdf_unit!r}): {exc}")
        return None


def detect_source_from_columns(
    columns: list[str],
    index: dict[str, dict[str, tuple[str, str]]],
) -> str | None:
    """Score each source by synonym hits; return the highest-scoring source."""
    config = load_config()
    best_source = None
    best_score = 0

    for source_id, source_index in index.items():
        regexes = [re.compile(r) for r in config["sources"][source_id].get("qty_unit_regexes", [])]
        score = 0
        for col in columns:
            col_lower = col.lower()
            qty, _ = extract_qty_unit(col_lower, regexes)
            if qty in source_index or col_lower in source_index:
                score += 1
        if score > best_score:
            best_score = score
            best_source = source_id

    return best_source if best_score > 0 else None


def normalize(
    df: Union[pl.DataFrame, pl.LazyFrame],
    source: str | None = None,
) -> tuple[Union[pl.DataFrame, pl.LazyFrame], dict]:
    """
    Map vendor columns to BDF canonical names with unit conversion and dtype casting.

    Returns (df_out, metadata) where metadata has "source" and "columns" provenance.
    """
    config = load_config()
    index = get_synonym_index()
    columns = df.columns

    if source is None:
        source = detect_source_from_columns(columns, index)

    metadata: dict = {"source": source, "columns": {}}

    if source is None:
        return df, metadata

    source_spec = config["sources"].get(source, {})
    regexes = [re.compile(r) for r in source_spec.get("qty_unit_regexes", [])]
    decimal = _sniff_decimal(df)
    source_dt_fmts = source_spec.get("datetime_formats", [])
    global_dt_fmts = config.get("datetime_formats", [])
    source_index = index.get(source, {})
    col_defs = config["columns"]
    schema = df.schema

    exprs: list[pl.Expr] = []
    seen_labels: set[str] = set()

    for col in columns:
        col_lower = col.lower()
        qty_lower, _ = extract_qty_unit(col_lower, regexes)
        _, src_unit_raw = extract_qty_unit(col, regexes)

        bdf_key_unit = source_index.get(qty_lower) or source_index.get(col_lower)

        if bdf_key_unit is None:
            exprs.append(pl.col(col))
            continue

        bdf_key, bdf_unit = bdf_key_unit
        bdf_label = col_defs[bdf_key]["label"]
        dtype_str = col_defs[bdf_key].get("dtype", "float")

        if bdf_label in seen_labels:
            # Already mapped — keep input column unchanged under original name
            exprs.append(pl.col(col))
            continue
        seen_labels.add(bdf_label)

        metadata["columns"][bdf_label] = {
            "source_header": col,
            "source_unit": src_unit_raw,
            "bdf_unit": bdf_unit,
        }

        expr = pl.col(col)

        if bdf_key == "unix_time_second":
            all_fmts = source_dt_fmts + global_dt_fmts
            if all_fmts:
                dt_expr = pl.coalesce([
                    pl.col(col).str.to_datetime(fmt, strict=False)
                    for fmt in all_fmts
                ])
                expr = dt_expr.dt.timestamp("ms").cast(pl.Float64) / 1000.0
            else:
                expr = expr.cast(pl.Float64, strict=False)
        else:
            col_dtype = schema.get(col)
            is_str = col_dtype in (pl.String, pl.Utf8)

            if decimal != "." and is_str:
                expr = expr.str.replace_all(decimal, ".", literal=True)

            expr = expr.cast(pl.Float64, strict=False)

            factor = pint_factor(src_unit_raw, bdf_unit)
            if factor is not None and factor != 1.0:
                expr = expr * factor

            if dtype_str == "int":
                expr = expr.cast(pl.Int64, strict=False)

        exprs.append(expr.alias(bdf_label))

    df_out = df.select(exprs)
    return df_out, metadata


def normalize_pandas(df) -> tuple:
    """Convenience wrapper: pandas DataFrame → normalize → pandas DataFrame."""
    import pandas  # noqa: F401
    pl_df = pl.from_pandas(df)
    pl_out, meta = normalize(pl_df)
    return pl_out.to_pandas(), meta
