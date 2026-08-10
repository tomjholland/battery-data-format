"""Datetime format handling shared by the table and metadata halves of a plugin.

The same vendor software writes a file's preamble and its timestamp column, so
both halves parse with the same per-vendor format constants (``_ARBIN_DT_FMTS``,
``_NEWARE_DT_FMTS``, ...). Keeping the split and the coercion here means a
preamble ``started_at`` and a ``Unix Time / s`` column cannot disagree because
one path used chrono and the other used ``datetime.strptime``: both go through
polars.

Depends on polars alone, so :mod:`bdf.metadata_parsers` can use it without
importing any table module.
"""

from __future__ import annotations

import re
from typing import Literal, Sequence

import polars as pl

# Formats carrying an offset directive describe their own timezone; the rest are
# naive and have to be localised by the caller. ``%Z`` counts as self-describing
# because the table path has always treated it so, but polars parses a zone
# *name* without applying its offset — no BDF format declares ``%Z`` today, and
# one should not be added without revisiting this split.
TZ_COMPONENT_RE = re.compile(r"%:?[zZ]")

DST_AMBIGUOUS_STRATEGY: Literal["earliest"] = "earliest"
DST_NON_EXISTENT_STRATEGY: Literal["null"] = "null"


def split_tz_fmts(fmts: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split format strings into (tz_aware, naive) by embedded offset directive.

    Args:
        fmts: Datetime format strings to classify.

    Returns:
        Tuple of (formats with %z/%:z/%Z, formats without).
    """
    tz_aware = [f for f in fmts if TZ_COMPONENT_RE.search(f)]
    naive = [f for f in fmts if not TZ_COMPONENT_RE.search(f)]
    return tz_aware, naive


def to_epoch_seconds(text: str, fmts: Sequence[str], tz: str) -> tuple[int | None, bool]:
    """Parse ``text`` with the first of ``fmts`` that matches, as integer epoch seconds.

    Self-describing formats are tried first and used as-is; naive formats are
    localised to ``tz``, matching the column path's coalesce order in
    :func:`bdf.table_normalizers._datetime_unix_expr`.

    Args:
        text: The datetime text captured from a preamble.
        fmts: Candidate format strings, tried in order within each group.
        tz: IANA timezone applied to naive candidates.

    Returns:
        Tuple of (epoch seconds, whether a naive format produced the value).
        The seconds are None when no candidate parsed ``text``, which callers
        treat as "the field is unstated" rather than as an error.
    """
    tz_aware_fmts, naive_fmts = split_tz_fmts(fmts)
    series = pl.Series("value", [text], dtype=pl.String)

    for fmt in tz_aware_fmts:
        epoch = _timestamp(series.str.to_datetime(fmt, strict=False))
        if epoch is not None:
            return epoch, False

    for fmt in naive_fmts:
        parsed = series.str.to_datetime(fmt, strict=False)
        if parsed.null_count():
            continue
        localised = parsed.dt.replace_time_zone(
            tz,
            ambiguous=DST_AMBIGUOUS_STRATEGY,
            non_existent=DST_NON_EXISTENT_STRATEGY,
        )
        epoch = _timestamp(localised)
        if epoch is not None:
            return epoch, True

    return None, False


def _timestamp(parsed: pl.Series) -> int | None:
    """Return the single value of ``parsed`` as whole epoch seconds, or None if null.

    Args:
        parsed: One-element datetime series.

    Returns:
        Epoch seconds, or None when the format did not parse.
    """
    micros = parsed.dt.timestamp("us").item()
    return None if micros is None else micros // 1_000_000
