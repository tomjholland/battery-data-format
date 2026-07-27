"""Canonical data filename convention: ``inst__cell__{date}_{test}.bdf.csv``.

Three double-underscore (``__``) separated segments — institution, cell id,
and a third segment fusing ``{date}_{test}`` on the first underscore. Ambient
conditions (and any replicate/qualifier) fold into the test name rather than
forming a separate segment.

Standalone module: :func:`parse`/:func:`format` are not wired into
``bdf.ingest`` this change — ``ingest`` keeps its own filename helpers for now
(deferred convergence).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass(frozen=True)
class Parts:
    """The parsed components of a canonical data filename.

    Attributes:
        institution: Contributor/institution segment.
        cell: Cell id segment.
        date: Leading date token of the fused third segment.
        test: Test name — the remainder of the fused third segment after its
            first underscore (ambient conditions and replicates included).
        ext: Filename extension without its leading dot (e.g. ``"bdf.csv"``).
    """

    institution: str
    cell: str
    date: str
    test: str
    ext: str = "bdf.csv"


def _split_ext(base: str) -> tuple[str, str]:
    """Split a basename into its stem and (possibly multi-dot) extension.

    Args:
        base: A filename with no directory component.

    Returns:
        ``(stem, ext)`` where ``ext`` has no leading dot.

    Raises:
        ValueError: If ``base`` has no extension.
    """
    stem, sep, ext = base.partition(".")
    if not sep:
        raise ValueError(f"filename has no extension: {base!r}")
    return stem, ext


def _split_date_test(fused: str) -> tuple[str, str]:
    """Split a fused ``{date}_{test}`` segment on its first underscore.

    Args:
        fused: The third ``__``-delimited segment.

    Returns:
        ``(date, test)``.

    Raises:
        ValueError: If ``fused`` carries no underscore to split on.
    """
    date, sep, test = fused.partition("_")
    if not sep:
        raise ValueError(f"cannot split date/test from segment: {fused!r}")
    return date, test


def parse(name: Union[str, Path]) -> Parts:
    """Parse a canonical (or README-variant) data filename into its parts.

    The canonical form is three ``__``-separated segments, the third fusing
    ``{date}_{test}``. A four-segment ``inst__cell__date__NNN`` variant (no
    fused test name) is tolerated on read per Decision 9, but never emitted by
    :func:`format`.

    Args:
        name: A filename or path; only the basename is inspected.

    Returns:
        The parsed :class:`Parts`.

    Raises:
        ValueError: If ``name`` does not match either accepted shape.
    """
    base = Path(name).name
    stem, ext = _split_ext(base)
    segments = stem.split("__")
    if len(segments) == 3:
        institution, cell, fused = segments
        date, test = _split_date_test(fused)
    elif len(segments) == 4:
        institution, cell, date, test = segments
    else:
        raise ValueError(f"cannot parse filename into inst__cell__{{date}}_{{test}} form: {name!r}")
    return Parts(institution=institution, cell=cell, date=date, test=test, ext=ext)


def format(parts: Parts) -> str:
    """Format canonical :class:`Parts` back into a filename.

    Always emits the canonical three-segment form, the inverse of parsing that
    shape: ``parse(format(p)) == p``.

    Args:
        parts: The filename parts to format.

    Returns:
        The canonical filename.
    """
    return f"{parts.institution}__{parts.cell}__{parts.date}_{parts.test}.{parts.ext}"
