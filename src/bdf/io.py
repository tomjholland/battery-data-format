# src/bdf/io.py
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast

if TYPE_CHECKING:
    import pandas as pd
import polars as pl

from bdf._errors import BDFDefinitionError, BDFDefinitionWarning, BDFMetadataError, BDFMetadataWarning
from bdf._time_scale import detect_scale_mismatch
from bdf.battinfo import BATTINFO_SCHEMA, BattinfoSchema, _has_battinfo, _load_schema, get_stated_release
from bdf.file_utils import open_compressed, strip_compression_suffix
from bdf.metadata import Metadata
from bdf.metadata_parsers import BdfSidecarParser
from bdf.normalization import DayMonthOrder
from bdf.plugins import PLUGINS, Plugin, detect
from bdf.spec import COLUMN_ONTOLOGY, ONTOLOGY_SOURCE, ColumnOntology


def _assemble_metadata(
    path: str | Path,
    resolved_plugin: Plugin,
    *,
    source: str,
    tz: str,
    day_month_order: DayMonthOrder | None = None,
    validate: bool = True,
    strict: bool = True,
    battinfo: BattinfoSchema | None = None,
    stacklevel: int = 3,
) -> Metadata:
    """Take this read's metadata from exactly one source, never combined.

    Args:
        path: Local file path or URL being read.
        resolved_plugin: The plugin whose ``metadata_parser`` runs when no
            reserved sidecar sits beside ``path``.
        source: Origin recorded as ``bdf.source`` for metadata that the plugin
            parser made; a sidecar keeps the source it states.
        tz: IANA timezone forwarded to the plugin's metadata parser.
        day_month_order: Field order forwarded to the plugin's metadata parser.
        validate: Check the ``battinfo_*`` fields; False checks nothing and loads no BattINFO schema.
        strict: Raise where the stated BattINFO schema cannot be loaded or refuses a
            value; False warns and keeps the value.
        battinfo: BattINFO schema to check against, instead of the BattINFO
            schema in ``bdf.battinfo_version``.
        stacklevel: ``warnings.warn`` stack level that reaches the caller, counted from ``_read``.

    Returns:
        The reserved sidecar's ``Metadata`` where ``<stem>.metadata.json``
        exists beside ``path``, restored repair records included; the
        plugin parser's ``Metadata`` otherwise, with ``bdf.source`` and
        ``bdf.created_with`` set. Where a ``battinfo_*`` field holds data,
        ``bdf.battinfo_version`` names the version of ``battinfo``, else the one
        stated, else the bundled one. The caller still applies this read's own repairs.

    Raises:
        BDFMetadataError: A sidecar exists and cannot be restored. An empty
            ``Metadata`` therefore always means that no sidecar exists, and
            never a sidecar this read failed on. A later ``save()`` of that
            metadata would otherwise write over the file the read could not
            read.
        BDFMetadataError: With ``validate`` and ``strict``, the BattINFO schema
            used for the check refuses a value.
        BDFDefinitionError: With ``validate`` and ``strict``, the stated BattINFO
            schema cannot be loaded.
    """
    import bdf

    reserved = BdfSidecarParser()
    if reserved.matches(path):
        metadata = reserved.parse(path)
    else:
        preamble_lines = None
        if resolved_plugin.metadata_parser.uses_preamble_boundary:
            preamble = resolved_plugin.table_parser.preamble(path)
            preamble_lines = None if preamble is None else len(preamble)
        metadata = resolved_plugin.metadata_parser.parse(
            path, tz=tz, day_month_order=day_month_order, preamble_lines=preamble_lines
        )
        metadata.bdf.source = source
        metadata.bdf.created_with = bdf.__version__

    if not _has_battinfo(metadata):
        return metadata
    version = battinfo.version if battinfo is not None else get_stated_release(metadata) or BATTINFO_SCHEMA.version
    metadata.bdf.battinfo_version = version
    if not validate:
        return metadata
    schema = battinfo or _load_stated("battinfo", version, path, strict=strict, stacklevel=stacklevel + 1)
    lines = metadata.check(schema) if schema is not None else []
    if lines:
        assert schema is not None
        msg = (
            f"BattINFO metadata read from {path} has values that BattINFO schema {schema.version!r} refuses. "
            "Correct the values, or pass strict=False to read them with a warning:\n  " + "\n  ".join(lines)
        )
        if strict:
            raise BDFMetadataError(msg)
        warnings.warn(msg, BDFMetadataWarning, stacklevel=stacklevel + 1)
    return metadata


def _load_stated(
    kind: Literal["ontology", "battinfo"], stated: str, where: str | Path, *, strict: bool, stacklevel: int
) -> Any:
    """Load the ontology or BattINFO schema whose version the metadata states.

    Args:
        kind: Which definition data to load; also the argument that names other data.
        stated: The version that the metadata states.
        where: The file the call reads or writes, for the message.
        strict: Raise, rather than warn, where the version cannot be loaded.
        stacklevel: ``warnings.warn`` stack level, counted from this function's caller.

    Returns:
        The ``ColumnOntology`` or ``BattinfoSchema``, or None, with a
        ``BDFDefinitionWarning``, where it cannot be loaded and ``strict`` is False.

    Raises:
        BDFDefinitionError: If ``strict`` and the version cannot be loaded.
    """
    if kind == "ontology":
        name = "ontology"
        then = f"Using the bundled ontology {COLUMN_ONTOLOGY.ontology_version} for the column labels, without a column check."
    else:
        name, then = "BattINFO schema", "The BattINFO metadata is not checked."
    try:
        return _ontology_for(stated) if kind == "ontology" else _load_schema(stated)
    except BDFDefinitionError as exc:
        msg = f"Could not load {name} {stated} that {where}'s metadata states: {exc}."
        if strict:
            raise BDFDefinitionError(
                f"{msg} Connect or cache {name} {stated}, pass {kind}=, pass strict=False to skip the check, "
                "or pass validate=False."
            ) from exc
        warnings.warn(f"{msg} {then}", BDFDefinitionWarning, stacklevel=stacklevel + 1)
        return None


def _ontology_for(stated: str) -> ColumnOntology:
    """Return ontology ``stated``: the bundled one, or one from the cache or upstream.

    Raises:
        BDFDefinitionError: If ``stated`` is not a version, or that ontology cannot be loaded.
    """
    name = ONTOLOGY_SOURCE.normalise(stated)
    bundled = COLUMN_ONTOLOGY.ontology_version
    if bundled and name == ONTOLOGY_SOURCE.normalise(bundled):
        return COLUMN_ONTOLOGY
    return ColumnOntology.from_version(name)


def _read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    strict: bool = True,
    include_unknown: bool = False,
    lazy: bool = True,
    tz: str = "UTC",
    day_month_order: DayMonthOrder | None = None,
    reconcile_time: bool = False,
    ontology: ColumnOntology | None = None,
    battinfo: BattinfoSchema | None = None,
    stacklevel: int = 3,
) -> tuple[pl.DataFrame | pl.LazyFrame, Metadata, ColumnOntology]:
    """Read ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata, ontology)``.

    Private implementation behind the public `read` and `scan` functions.
    The metadata is assembled first, because the ontology in its
    ``bdf.ontology_version`` chooses the ontology (see :func:`_resolve_ontology`) that the table is
    normalized with. ``ontology`` in the result is the one the read used.

    Raises:
        ValueError: If ``plugin`` is not None, a str, or a Plugin instance.
    """
    plugin_id: str | None = None
    resolved_plugin: Plugin
    if plugin is None:
        plugin_id, resolved_plugin = detect(path)
    elif isinstance(plugin, str):
        plugin_id = plugin
        resolved_plugin = PLUGINS[plugin]
    elif isinstance(plugin, Plugin):
        resolved_plugin = plugin
    else:
        raise ValueError(f"invalid plugin argument: {plugin!r}")

    metadata = _assemble_metadata(
        path,
        resolved_plugin,
        source=plugin_id or "custom",
        tz=tz,
        day_month_order=day_month_order,
        validate=validate,
        strict=strict,
        battinfo=battinfo,
        stacklevel=stacklevel,
    )
    stated = metadata.bdf.ontology_version
    check = validate
    if ontology is not None:
        used = ontology
        metadata.bdf.ontology_version = ontology.ontology_version or None
    elif stated:
        # A frame needs an ontology to be read: where the stated one cannot be loaded, the
        # bundled one gives the labels, and the metadata still states the ontology it names.
        loaded = _load_stated("ontology", stated, path, strict=validate and strict, stacklevel=stacklevel)
        used = loaded or COLUMN_ONTOLOGY
        check = validate and loaded is not None
    else:
        used = COLUMN_ONTOLOGY

    bdf_df = resolved_plugin.table_parser.read(
        path,
        normalize=normalize,
        validate=check,
        strict=strict,
        include_unknown=include_unknown,
        lazy=lazy,
        tz=tz,
        day_month_order=day_month_order,
        ontology=used,
    )

    if normalize and (validate or reconcile_time):
        bdf_df, repairs = _reconcile_time_scale(
            bdf_df, reconcile_time=reconcile_time, strict=validate and strict, ontology=used
        )
        if repairs:
            metadata.bdf.time_reconciliation = repairs

    return bdf_df, metadata, used


# Rows sampled for the elapsed-vs-wall-clock scale estimate; a uniform unit
# error shows up in any contiguous slice, so bounding the sample keeps lazy
# reads cheap on large files.
_RECONCILE_SAMPLE_ROWS = 100_000


def _reconcile_time_scale(
    df: pl.DataFrame | pl.LazyFrame,
    *,
    reconcile_time: bool,
    strict: bool,
    ontology: ColumnOntology,
) -> tuple[pl.DataFrame | pl.LazyFrame, list[dict]]:
    """Detect elapsed-time columns stored in the wrong unit; repair only on request.

    Compares ``Test Time / s`` and ``Step Time / s`` increments against the
    independently recorded wall clock (``Unix Time / s``). Detection always
    runs; the fsck model applies to what happens on a mismatch:

    - ``reconcile_time=True`` and the ratio matches a known unit factor (see
      :data:`bdf._time_scale.KNOWN_SCALE_FACTORS`): the column is rescaled to
      seconds, the repair is recorded, and a ``UserWarning`` announces it.
    - otherwise, ``strict=True`` raises :class:`BDFValidationError`
      (loud failure, nothing modified) and ``strict=False`` downgrades to a
      ``UserWarning``.

    Args:
        df: Normalized BDF frame (eager or lazy).
        reconcile_time: Rescale columns whose mismatch matches a known unit factor.
        strict: Raise on unrepaired mismatches instead of warning.
        ontology: Ontology that gives the time labels.

    Returns:
        Tuple of (possibly rescaled frame, list of repair records). The list is
        empty when nothing was repaired.

    Raises:
        BDFValidationError: On an unrepaired mismatch when ``strict`` is True.
    """
    wall_label = ontology.unix_time_second.formatted_label
    elapsed_labels = (
        ontology.test_time_second.formatted_label,
        ontology.step_time_second.formatted_label,
    )

    columns = df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns
    if wall_label not in columns:
        return df, []
    present = [lbl for lbl in elapsed_labels if lbl in columns]
    if not present:
        return df, []

    sample = df.select([wall_label, *present]).head(_RECONCILE_SAMPLE_ROWS)
    if isinstance(sample, pl.LazyFrame):
        sample = sample.collect()
    wall = sample[wall_label].cast(pl.Float64).to_numpy()

    records: list[dict] = []
    rescale: list[pl.Expr] = []
    problems: list[str] = []
    for label in present:
        mismatch = detect_scale_mismatch(sample[label].cast(pl.Float64).to_numpy(), wall)
        if mismatch is None:
            continue
        if mismatch.unit_name:
            described = (
                f"'{label}' values appear to be {mismatch.unit_name}, not the declared seconds "
                f"(increments disagree with '{wall_label}' by ~{mismatch.ratio:g}x)"
            )
        else:
            described = (
                f"'{label}' increments disagree with '{wall_label}' increments by "
                f"~{mismatch.ratio:g}x, which matches no known unit"
            )
        if reconcile_time and mismatch.factor is not None:
            rescale.append(pl.col(label) / mismatch.factor)
            records.append(
                {
                    "column": label,
                    "declared_unit": "s",
                    "actual_unit": mismatch.unit_name,
                    "ratio_vs_wall_clock": mismatch.ratio,
                    "n_samples": mismatch.n_samples,
                    "action": f"divided by {mismatch.factor:g}",
                }
            )
            warnings.warn(
                f"{described}; rescaled to seconds as requested (reconcile_time=True). "
                f"Recorded in metadata.bdf.time_reconciliation.",
                UserWarning,
                stacklevel=4,
            )
        else:
            problems.append(described)

    if problems:
        detail = "; ".join(problems)
        if strict:
            from ._errors import BDFValidationError

            raise BDFValidationError(
                f"Elapsed-time/wall-clock mismatch: {detail}. Pass reconcile_time=True to "
                f"rescale known unit factors, strict=False to warn instead, or validate=False to skip the check."
            )
        warnings.warn(f"Elapsed-time/wall-clock mismatch: {detail}.", UserWarning, stacklevel=4)

    if rescale:
        df = df.with_columns(rescale)
    return df, records


def read(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    strict: bool = True,
    include_unknown: bool = False,
    tz: str = "UTC",
    day_month_order: DayMonthOrder | None = None,
    reconcile_time: bool = False,
    ontology: ColumnOntology | None = None,
    battinfo: BattinfoSchema | None = None,
) -> tuple[pl.DataFrame, Metadata]:
    """Read ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    Collects to a :class:`polars.DataFrame`; use :func:`scan` for a :class:`polars.LazyFrame`.

    Args:
        path: Local file path or http(s) URL to read.
        plugin: Plugin instance or registry id. Auto-detects if not set (default).
        normalize: Map vendor columns to BDF canonical names (default True); False returns
            raw source columns unchanged.
        validate: Check the columns against the ontology, the time columns against
            the wall clock, and the ``battinfo_*`` fields against their BattINFO schema
            (default True). False checks nothing, and loads no BattINFO schema.
        strict: With ``validate``, raise when a check fails (default True): missing
            required columns or a time-scale mismatch raise ``BDFValidationError``, a
            refused BattINFO value raises ``BDFMetadataError``, and an ontology or
            BattINFO schema that the sidecar states but bdf cannot load raises
            ``BDFDefinitionError``. False warns instead (``BDFMetadataWarning`` for the
            metadata, ``BDFDefinitionWarning`` for definition data, whose check is then
            skipped) and returns the data.
        include_unknown: Keep columns outside of the BDF spec in the dataframe (default False).
        tz: IANA timezone used to compute ``Unix Time / s`` if the source has naive datetime.
            Default is``"UTC"``, and will warn if source contains naive datetimes.
        day_month_order: Field order applied to an ambiguous numeric date the table
            column and the staged metadata each declare. ``"day_first"`` reads it day
            then month, ``"month_first"`` reads it month then day; ``None`` (default)
            leaves every declared format unchanged.
        reconcile_time: Elapsed-time columns are cross-checked against wall-clock
            increments when both are present (e.g. a vendor export storing milliseconds
            under a seconds header, GH #65). A mismatch raises ``BDFValidationError`` by
            default (warns when ``strict=False``, skipped when ``validate=False``); pass ``reconcile_time=True`` to
            explicitly rescale known unit factors, recorded under
            ``metadata.bdf.time_reconciliation``. Only active when ``normalize=True``.
        ontology: Ontology that gives the labels, units, dtypes and required columns.
            Defaults to the ontology in the sidecar's ``bdf.ontology_version``,
            loaded from the bundle, the cache, or upstream; to the bundled ontology when
            there is no sidecar or it states none. Where that ontology cannot be loaded
            (and the read does not raise, see ``strict``), the bundled ontology gives
            the labels and the columns are not checked. The returned
            ``bdf.ontology_version`` names ``ontology`` where it is given, else the
            ontology the sidecar states.
        battinfo: BattINFO schema to check the ``battinfo_*`` fields against. Defaults to
            the BattINFO schema in the sidecar's ``bdf.battinfo_version`` (or the one a
            bdf 0.2.0 ``battinfo_ref`` names), loaded from the bundle, the cache, or
            upstream with a 5 s deadline; to the bundled BattINFO schema otherwise.
            Where that schema cannot be loaded (and the read does not raise, see
            ``strict``), the fields are not checked. The returned ``bdf.battinfo_version``
            names the version of ``battinfo`` where it is given, else the version the
            sidecar states, else the bundled one.

    Returns:
        Tuple of (df, metadata): the BDF table as a DataFrame, and a ``Metadata``
        carrying the six ``battinfo_*`` fields, ``bdf`` (with at least ``source`` naming the
        resolved plugin id, ``"custom"`` for a directly-supplied ``Plugin``), ``raw``,
        and ``extras``.

    Raises:
        ValueError: If ``plugin`` is not None, a str, or a Plugin instance.
        BDFMetadataError: With validate and strict, the BattINFO schema used for the check refuses a value of the metadata.
        BDFDefinitionError: With validate and strict, the ontology or BattINFO schema that the sidecar states cannot be loaded.
    """
    bdf_df, metadata, _ = _read(
        path,
        plugin=plugin,
        normalize=normalize,
        validate=validate,
        strict=strict,
        include_unknown=include_unknown,
        lazy=False,
        tz=tz,
        day_month_order=day_month_order,
        reconcile_time=reconcile_time,
        ontology=ontology,
        battinfo=battinfo,
    )
    return cast(pl.DataFrame, bdf_df), metadata


def scan(
    path: str | Path,
    *,
    plugin: Plugin | str | None = None,
    normalize: bool = True,
    validate: bool = True,
    strict: bool = True,
    include_unknown: bool = False,
    tz: str = "UTC",
    day_month_order: DayMonthOrder | None = None,
    reconcile_time: bool = False,
    ontology: ColumnOntology | None = None,
    battinfo: BattinfoSchema | None = None,
) -> tuple[pl.LazyFrame, Metadata]:
    """Scan ``path`` (local file or URL) to BDF-canonical form, returning ``(df, metadata)``.

    Returns a :class:`polars.LazyFrame`; use :func:`read` for an eager :class:`polars.DataFrame`.

    Laziness depends on the plugin: CSV/Parquet parsers scan lazily with real pushdown; binary
    formats (.xlsx, .nda, .ndax, .mat, .mpr) read eagerly and just wrap the result in a
    LazyFrame — harmless, but no performance benefit.

    Args:
        path: Local file path or http(s) URL to read.
        plugin: Plugin instance or registry id; auto-detects via ``bdf.plugins.detect`` when
            None (default).
        normalize: Map vendor columns to BDF canonical names (default True); False returns
            raw source columns unchanged.
        validate: Check the columns against the ontology, the time columns against
            the wall clock, and the ``battinfo_*`` fields against their BattINFO schema
            (default True). False checks nothing, and loads no BattINFO schema.
        strict: With ``validate``, raise when a check fails (default True): missing
            required columns or a time-scale mismatch raise ``BDFValidationError``, a
            refused BattINFO value raises ``BDFMetadataError``, and an ontology or
            BattINFO schema that the sidecar states but bdf cannot load raises
            ``BDFDefinitionError``. False warns instead (``BDFMetadataWarning`` for the
            metadata, ``BDFDefinitionWarning`` for definition data, whose check is then
            skipped) and returns the data.
        include_unknown: Keep columns outside of the BDF spec in the dataframe (default False).
        tz: IANA timezone used to compute ``Unix Time / s`` if the source has naive datetime.
            Default is``"UTC"``, and will warn if source contains naive datetimes.
        day_month_order: Field order applied to an ambiguous numeric date the table
            column and the staged metadata each declare. ``"day_first"`` reads it day
            then month, ``"month_first"`` reads it month then day; ``None`` (default)
            leaves every declared format unchanged. Fixed when the expression is
            built: no row is read to choose it, and the choice is never deferred to
            ``collect()``.
        reconcile_time: Elapsed-time columns are cross-checked against wall-clock
            increments when both are present (e.g. a vendor export storing milliseconds
            under a seconds header, GH #65). A mismatch raises ``BDFValidationError`` by
            default (warns when ``strict=False``, skipped when ``validate=False``); pass ``reconcile_time=True`` to
            explicitly rescale known unit factors, recorded under
            ``metadata.bdf.time_reconciliation``. Only active when ``normalize=True``.
        ontology: Ontology that gives the labels, units, dtypes and required columns.
            Defaults to the ontology in the sidecar's ``bdf.ontology_version``,
            loaded from the bundle, the cache, or upstream; to the bundled ontology when
            there is no sidecar or it states none. Where that ontology cannot be loaded
            (and the read does not raise, see ``strict``), the bundled ontology gives
            the labels and the columns are not checked. The returned
            ``bdf.ontology_version`` names ``ontology`` where it is given, else the
            ontology the sidecar states.
        battinfo: BattINFO schema to check the ``battinfo_*`` fields against. Defaults to
            the BattINFO schema in the sidecar's ``bdf.battinfo_version`` (or the one a
            bdf 0.2.0 ``battinfo_ref`` names), loaded from the bundle, the cache, or
            upstream with a 5 s deadline; to the bundled BattINFO schema otherwise.
            Where that schema cannot be loaded (and the read does not raise, see
            ``strict``), the fields are not checked. The returned ``bdf.battinfo_version``
            names the version of ``battinfo`` where it is given, else the version the
            sidecar states, else the bundled one.

    Returns:
        Tuple of (df, metadata): the BDF table as a LazyFrame, and a ``Metadata``
        carrying the six ``battinfo_*`` fields, ``bdf`` (with at least ``source`` naming the
        resolved plugin id, ``"custom"`` for a directly-supplied ``Plugin``), ``raw``,
        and ``extras``.

    Raises:
        ValueError: If ``plugin`` is not None, a str, or a Plugin instance.
        BDFMetadataError: With validate and strict, the BattINFO schema used for the check refuses a value of the metadata.
        BDFDefinitionError: With validate and strict, the ontology or BattINFO schema that the sidecar states cannot be loaded.
    """
    bdf_df, metadata, _ = _read(
        path,
        plugin=plugin,
        normalize=normalize,
        validate=validate,
        strict=strict,
        include_unknown=include_unknown,
        lazy=True,
        tz=tz,
        day_month_order=day_month_order,
        reconcile_time=reconcile_time,
        ontology=ontology,
        battinfo=battinfo,
    )
    return cast(pl.LazyFrame, bdf_df), metadata


class _ArtifactFormat(NamedTuple):
    """How one BDF artifact format is recognized and written.

    Attributes:
        extensions: Suffixes that name the format, compression suffix removed.
        write: ``polars.DataFrame`` method that writes an eager frame.
        sink: ``polars.LazyFrame`` method that streams a lazy frame straight to
            the target, or None where polars has no sink for the format. A
            format with no sink collects the frame first.
        compressible: False where the writer needs a real file path, so a
            compressed target is an error.
    """

    extensions: tuple[str, ...]
    write: str
    sink: str | None
    compressible: bool = True


_FORMATS: dict[str, _ArtifactFormat] = {
    "csv": _ArtifactFormat((".csv", ".bdf.csv"), "write_csv", "sink_csv"),
    "parquet": _ArtifactFormat((".parquet", ".bdf.parquet", ".pq", ".bdf.pq"), "write_parquet", "sink_parquet"),
    "ipc": _ArtifactFormat(
        (".ipc", ".bdf.ipc", ".feather", ".bdf.feather", ".ftr", ".bdf.ftr", ".arrow", ".bdf.arrow"),
        "write_ipc",
        "sink_ipc",
    ),
    "json": _ArtifactFormat((".json", ".bdf.json"), "write_json", None),
    "ndjson": _ArtifactFormat((".ndjson", ".bdf.ndjson"), "write_ndjson", "sink_ndjson"),
    "xlsx": _ArtifactFormat((".xlsx", ".bdf.xlsx"), "write_excel", None, compressible=False),
}


def _detect_format(path: Path) -> str:
    """Return the BDF artifact format ("csv"/"parquet"/"ipc"/"json"/"ndjson"/"xlsx") for ``path``.

    Args:
        path: File path whose suffixes are inspected (e.g. ``.bdf.csv.gz``).

    Returns:
        Format name whose extensions in :data:`_FORMATS` match ``path``.

    Raises:
        ValueError: If no known format extension is found in ``path``.
    """
    sfx = "".join(Path(strip_compression_suffix(path.name)).suffixes).lower()
    for fmt, spec in _FORMATS.items():
        if any(sfx.endswith(e) for e in spec.extensions):
            return fmt
    raise ValueError(f"Unknown BDF artifact format: {path.name}")


def _as_polars(df: pl.DataFrame | pl.LazyFrame | pd.DataFrame) -> pl.DataFrame | pl.LazyFrame:
    """Return ``df`` as a polars frame, keeping a LazyFrame lazy.

    Args:
        df: Table to write, polars eager, polars lazy, or pandas.

    Returns:
        ``df`` unchanged where it is already a polars frame, a
        ``polars.DataFrame`` built from it otherwise.
    """
    if isinstance(df, (pl.DataFrame, pl.LazyFrame)):
        return df
    return pl.DataFrame(df)


def _version_stamps(ontology_version: str | None, battinfo_version: str | None) -> dict[str, str | None]:
    """Return the stamps a written sidecar carries (GH #106).

    Args:
        ontology_version: The ontology version that the sidecar states: that of
            ``ontology=``, else the one the metadata states, else the bundled one.
        battinfo_version: The BattINFO schema version that the sidecar states: that
            of ``battinfo=``, else the one the metadata states, else the bundled one;
            None where every ``battinfo_*`` field is empty.

    Returns:
        ``bdf_version`` (the installed package version), ``ontology_version``
        and ``battinfo_version``.
    """
    import bdf

    return {
        "bdf_version": bdf.__version__,
        "ontology_version": ontology_version,
        "battinfo_version": battinfo_version,
    }


_WRITER_STAMPS = ("bdf_version", "ontology_version", "battinfo_version")


def _sidecar_text(metadata: Metadata, ontology_version: str | None, battinfo_version: str | None) -> str | None:
    """Return the sidecar text for ``metadata``, or None where it carries nothing.

    The text is stamped with the writer's versions (GH #106):
    ``bdf.bdf_version``, ``bdf.ontology_version`` and ``bdf.battinfo_version``
    (see :func:`_version_stamps`). The stamps identify the file's writer, so they replace any stamps
    the caller's object carries. The caller's object is not modified. A ``Metadata`` that carries nothing gives None:
    the stamps describe a sidecar, so they never create one.

    Args:
        metadata: Metadata to write. Only the values that differ from their
            defaults reach the text.
        ontology_version: The ontology version that the sidecar states.
        battinfo_version: The BattINFO schema version that the sidecar states.

    Raises:
        ValueError: A value that JSON cannot hold, such as a ``numpy.int64``.
    """
    import bdf

    payload = metadata.model_dump(mode="json", exclude_defaults=True)
    if not payload:
        return None
    stamps = payload.setdefault("bdf", {})
    for key in _WRITER_STAMPS:
        stamps.pop(key, None)
    if "source" not in stamps and "created_with" not in stamps:
        stamps["created_with"] = bdf.__version__
    stamps.update({k: v for k, v in _version_stamps(ontology_version, battinfo_version).items() if v})
    return json.dumps(payload, ensure_ascii=False, indent=2)


def save(
    df: pl.DataFrame | pl.LazyFrame | pd.DataFrame,
    pathlike: str | Path,
    *,
    metadata: Metadata | None = None,
    validate: bool = True,
    strict: bool = True,
    labels: Literal["preferred", "machine", "unchanged"] = "unchanged",
    ontology: ColumnOntology | None = None,
    battinfo: BattinfoSchema | None = None,
    **opts,
) -> None:
    """Save a BDF table to a CSV/parquet/IPC/JSON/ndjson/xlsx artifact.

    Detects format and compression from the file extension and creates parent
    directories as needed.

    A ``LazyFrame`` reaches the target through the polars ``sink_*`` writer of
    the format, so polars streams the table and does not materialize it first.
    JSON and xlsx have no sink, so a ``LazyFrame`` collects for those two
    formats.

    Args:
        df: BDF table to write.
        pathlike: Output file path; format/compression are inferred from its extension.
        metadata: Optional ``Metadata`` written alongside as a ``.metadata.json``
            sidecar (``mydata.bdf.parquet`` pairs with ``mydata.bdf.metadata.json``).
            A ``Metadata`` carrying nothing deletes the sidecar, so the artifact
            keeps no metadata. Omit the argument only where the target has no
            sidecar: a save that omits it beside an existing sidecar raises,
            because the sidecar describes the data the previous save wrote. The
            message states each out, one of which is a save to a different path.
        validate: Check the columns against the ontology and the ``battinfo_*`` fields of
            ``metadata`` against their BattINFO schema (default True). False checks
            nothing and loads no BattINFO schema, and no ontology unless ``labels``
            renames columns; both files are written.
        strict: With ``validate``, raise when a check fails, or when the ontology or
            BattINFO schema that ``metadata`` states cannot be loaded, and write nothing
            (default True). False warns instead, skips the check of definition data
            that cannot be loaded, and writes both files.
        labels: Style of column names to use (default: "unchanged"):
            "preferred": BDF preferred label, e.g. "Voltage / V"
            "machine": BDF machine-readable label e.g. "voltage_volt"
            "unchanged": Keep column names as-is
        ontology: Ontology to check the columns against; the sidecar states its version.
            Defaults to the ontology in ``metadata``'s ``bdf.ontology_version`` (so a
            read and then a save keeps the file's ontology), else the bundled ontology.
            The sidecar states that version whether or not bdf can load it.
        battinfo: BattINFO schema to check the ``battinfo_*`` fields of ``metadata``
            against; the sidecar states its version. Defaults to the BattINFO schema
            in ``metadata``'s ``bdf.battinfo_version``, else the bundled one. The
            sidecar states that version whether or not bdf can load it.
        **opts: Additional keyword arguments forwarded to the polars writer
            (``write_csv``/``write_parquet``/``write_ipc``/``write_json``/``write_ndjson``/
            ``write_excel``), or to the matching ``sink_*`` writer where ``df`` is a
            streamed ``LazyFrame``.

    Raises:
        BDFDefinitionError: If ``validate`` and ``strict``, and the ontology in
            ``bdf.ontology_version`` or the BattINFO schema in ``bdf.battinfo_version``
            of ``metadata`` cannot be loaded. Nothing is written.
        ValueError: If the format is unsupported, or compression is requested for xlsx output.
        FileExistsError: If ``metadata`` is omitted and a ``.metadata.json`` sidecar
            already sits beside the target.
        BDFMetadataError: With ``validate`` and ``strict``, the BattINFO schema used for the
            check refuses a value of ``metadata``. Neither file is written.
        ValueError: ``metadata`` holds a value that JSON cannot hold. Neither file is written.
    """
    p = Path(pathlike)
    sidecar = BdfSidecarParser().sidecar_path(p)
    if metadata is None and sidecar.exists():
        msg = (
            f"{sidecar} describes the data a previous save wrote, and this save states no metadata. "
            "Pass metadata= to keep or update it, or metadata=Metadata() to clear it. "
            "To keep both the sidecar and the data it describes, save to a different path. "
            "To discard it, delete the sidecar."
        )
        raise FileExistsError(msg)

    fmt = _detect_format(p)
    spec = _FORMATS[fmt]
    compressed = strip_compression_suffix(p.name) != p.name
    if compressed and not spec.compressible:
        msg = f"Compression is not supported for {fmt} output"
        raise ValueError(msg)

    stated = metadata.bdf.ontology_version if metadata is not None else None
    ontology_version = (
        (ontology.ontology_version or None) if ontology is not None else stated or COLUMN_ONTOLOGY.ontology_version
    )
    frame = _as_polars(df)
    used = ontology
    if used is None and stated and (validate or labels != "unchanged"):
        used = _load_stated("ontology", stated, p, strict=validate and strict, stacklevel=2)
        if used is not None and validate:
            used.validate_df(frame, raise_on_error=strict)
    elif validate:
        (used or COLUMN_ONTOLOGY).validate_df(frame, raise_on_error=strict)
    frame = (used or COLUMN_ONTOLOGY).rename_labels(frame, labels)

    text: str | None = None
    if metadata is not None:
        battinfo_version = None
        if _has_battinfo(metadata):
            battinfo_version = (
                battinfo.version if battinfo is not None else get_stated_release(metadata) or BATTINFO_SCHEMA.version
            )
            schema = battinfo
            if validate and schema is None:
                schema = _load_stated("battinfo", battinfo_version, sidecar, strict=strict, stacklevel=2)
            lines = metadata.check(schema) if validate and schema is not None else []
            if lines:
                assert schema is not None
                msg = (
                    f"The BattINFO metadata for {sidecar} does not pass the check against BattINFO schema {schema.version!r}. "
                    "Correct the values, set metadata.bdf.battinfo_version to the version they follow, "
                    "or pass strict=False to write them with a warning:\n  " + "\n  ".join(lines)
                )
                if strict:
                    raise BDFMetadataError(msg)
                warnings.warn(msg, BDFMetadataWarning, stacklevel=2)
        text = _sidecar_text(metadata, ontology_version, battinfo_version)

    if isinstance(frame, pl.LazyFrame) and spec.sink is not None:
        writer = getattr(frame, spec.sink)
    else:
        if isinstance(frame, pl.LazyFrame):
            frame = frame.collect()
        writer = getattr(frame, spec.write)

    p.parent.mkdir(parents=True, exist_ok=True)
    target: Any = open_compressed(p)
    try:
        writer(target, **opts)
    finally:
        if not isinstance(target, Path):
            target.close()

    if text is not None:
        sidecar.write_text(text, encoding="utf-8")
    elif metadata is not None:
        sidecar.unlink(missing_ok=True)
