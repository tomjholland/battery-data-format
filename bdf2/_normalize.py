"""Column mapping, unit conversion, and type coercion."""

from __future__ import annotations

import warnings
from typing import Union

import polars as pl
import pint

from ._config import extract_qty_unit, get_source_regexes, get_synonym_index, load_config

_ureg = pint.UnitRegistry()

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
            comma += sample[col].str.count_matches(r'\d+,\d+').sum()
            dot += sample[col].str.count_matches(r'\d+\.\d+').sum()

    return "," if comma > dot else "."


def _build_col_expr(
    src_col: str,
    bdf_key: str,
    bdf_unit: str,
    dtype_str: str,
    src_unit_raw: str | None,
    schema: dict,
    decimal: str,
    all_dt_fmts: list[str],
) -> pl.Expr:
    expr = pl.col(src_col)
    if bdf_key == "unix_time_second":
        if all_dt_fmts:
            expr = pl.coalesce([
                pl.col(src_col).str.to_datetime(fmt, strict=False)
                for fmt in all_dt_fmts
            ]).dt.timestamp("ms").cast(pl.Float64) / 1000.0
        else:
            expr = expr.cast(pl.Float64, strict=False)
    else:
        if decimal != "." and schema.get(src_col) in (pl.String, pl.Utf8):
            expr = expr.str.replace_all(decimal, ".", literal=True)
        expr = expr.cast(pl.Float64, strict=False)
        factor = pint_factor(src_unit_raw, bdf_unit)
        if factor is not None and factor != 1.0:
            expr = expr * factor
        if dtype_str == "int":
            expr = expr.cast(pl.Int64, strict=False)
    return expr


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
    best_source = None
    best_score = 0

    source_regexes = get_source_regexes()
    for source_id, source_index in index.items():
        regexes = source_regexes.get(source_id, [])
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
    *,
    column_map: dict[str, str] | None = None,
    include_optional: bool = True,
    extra_columns: dict[str, str] | None = None,
) -> tuple[Union[pl.DataFrame, pl.LazyFrame], dict]:
    """
    Map vendor columns to BDF canonical names with unit conversion and dtype casting.

    Returns (df_out, metadata) where metadata has "source" and "columns" provenance.
    """
    config = load_config()
    col_defs = config["columns"]
    label_to_key: dict[str, str] = {v["label"]: k for k, v in col_defs.items()}

    if column_map:
        for lbl in column_map:
            if lbl not in label_to_key:
                raise ValueError(f"column_map key {lbl!r} is not a valid BDF label")

    index = get_synonym_index()
    columns = df.columns

    if source is None:
        source = detect_source_from_columns(columns, index)

    metadata: dict = {"source": source, "columns": {}}

    if source is None:
        return df, metadata

    source_spec = config["sources"].get(source, {})
    regexes = get_source_regexes().get(source, [])
    decimal = _sniff_decimal(df)
    source_dt_fmts = source_spec.get("datetime_formats", [])
    global_dt_fmts = config.get("datetime_formats", [])
    source_index = index.get(source, {})
    schema = df.schema

    all_dt_fmts = source_dt_fmts + global_dt_fmts
    exprs: list[pl.Expr] = []
    seen_labels: set[str] = set()
    column_map_sources: set[str] = set(column_map.values()) if column_map else set()

    if column_map:
        for bdf_label, src_col in column_map.items():
            bdf_key = label_to_key[bdf_label]
            if not include_optional and col_defs[bdf_key].get("required") is False:
                continue
            bdf_unit = col_defs[bdf_key]["unit"]
            dtype_str = col_defs[bdf_key].get("dtype", "float")
            _, src_unit_raw = extract_qty_unit(src_col, regexes)

            metadata["columns"][bdf_label] = {
                "source_header": src_col,
                "source_unit": src_unit_raw,
                "bdf_unit": bdf_unit,
            }

            exprs.append(
                _build_col_expr(src_col, bdf_key, bdf_unit, dtype_str, src_unit_raw, schema, decimal, all_dt_fmts)
                .alias(bdf_label)
            )
            seen_labels.add(bdf_label)

    for col in columns:
        if col in column_map_sources:
            continue

        col_lower = col.lower()
        qty_raw, src_unit_raw = extract_qty_unit(col, regexes)
        qty_lower = qty_raw.lower()

        bdf_key_unit = source_index.get(qty_lower) or source_index.get(col_lower)

        if bdf_key_unit is None:
            exprs.append(pl.col(col))
            continue

        bdf_key, bdf_unit = bdf_key_unit

        if not include_optional and col_defs[bdf_key].get("required") is False:
            continue

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

        exprs.append(
            _build_col_expr(col, bdf_key, bdf_unit, dtype_str, src_unit_raw, schema, decimal, all_dt_fmts)
            .alias(bdf_label)
        )

    if extra_columns:
        for src, out in extra_columns.items():
            if src not in columns:
                warnings.warn(
                    f"extra_columns source {src!r} not in DataFrame columns; skipping",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            exprs.append(pl.col(src).alias(out))

    df_out = df.select(exprs)
    return df_out, metadata


def normalize_pandas(df) -> tuple:
    """Convenience wrapper: pandas DataFrame → normalize → pandas DataFrame."""
    pl_df = pl.from_pandas(df)
    pl_out, meta = normalize(pl_df)
    return pl_out.to_pandas(), meta
