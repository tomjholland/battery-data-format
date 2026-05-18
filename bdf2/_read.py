"""Top-level dispatch: select a reader by extension/instance/config path and run it."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from .readers import BaseReader, CSVReader, ExcelReader, MATReader
from .schema import BDFColumn, ResolvedColumn

_CSV_EXTS = {".csv", ".tsv", ".txt", ".dat"}
_EXCEL_EXTS = {".xlsx", ".xlsm", ".xls"}
_MAT_EXTS = {".mat"}


def _reader_class_for(path: Path) -> type[BaseReader]:
    ext = path.suffix.lower()
    if ext in _CSV_EXTS:
        return CSVReader
    if ext in _EXCEL_EXTS:
        return ExcelReader
    if ext in _MAT_EXTS:
        return MATReader
    return CSVReader


def _build_mat_column_map(column_map: dict[str, str] | None) -> dict[BDFColumn, ResolvedColumn] | None:
    if not column_map:
        return None
    mr_to_col = {c.mr_name: c for c in BDFColumn}
    out: dict[BDFColumn, ResolvedColumn] = {}
    for mr_name, src in column_map.items():
        if mr_name not in mr_to_col:
            raise ValueError(f"column_map key {mr_name!r} is not a valid BDF mr_name")
        col = mr_to_col[mr_name]
        out[col] = ResolvedColumn(
            source_header=src, bdf_unit=col.unit, scale=1.0, offset=0.0,
        )
    return out


def read(
    path: str | Path,
    source: str | None = None,
    *,
    reader: BaseReader | str | Path | None = None,
    lazy: bool = False,
    column_map: dict[str, str] | None = None,
    include_optional: bool = True,
    extra_columns: dict[str, str] | None = None,
) -> tuple[pl.DataFrame | pl.LazyFrame, dict]:
    """Read a battery cycler file and return ``(bdf_df, metadata)``.

    Selects a :class:`bdf2.readers.BaseReader` by extension when ``reader`` is
    ``None`` (``.csv``/``.tsv``/``.txt``/``.dat`` → :class:`CSVReader`,
    ``.xlsx``/``.xlsm``/``.xls`` → :class:`ExcelReader`, ``.mat`` →
    :class:`MATReader`). A reader instance is used directly; a path argument is
    loaded via the subclass' :meth:`from_config_file` (subclass inferred from
    the data file's extension). For ``.mat`` files an explicit ``column_map`` is
    required.
    """
    path = Path(path)
    cls = _reader_class_for(path)

    if reader is None:
        instance: BaseReader
        if cls is MATReader:
            mat_cm = _build_mat_column_map(column_map)
            if mat_cm is None:
                raise ValueError(
                    f"{path}: .mat files require an explicit column_map "
                    f"(use bdf2.MATReader(column_map=...) or pass column_map=...)"
                )
            instance = MATReader(column_map=mat_cm, source=source)
            return instance.read(path, lazy=lazy)
        else:
            instance = cls(source=source, include_optional=include_optional, extra_columns=extra_columns)
    elif isinstance(reader, BaseReader):
        instance = reader
        if source is not None:
            instance.source = source  # type: ignore[assignment]
    elif isinstance(reader, (str, Path)):
        instance = cls.from_config_file(reader)
        if source is not None:
            instance.source = source  # type: ignore[assignment]
    else:
        raise TypeError(f"reader must be BaseReader | str | Path | None, got {type(reader).__name__}")

    if isinstance(instance, MATReader):
        return instance.read(path, lazy=lazy)
    return instance.read(path, lazy=lazy, column_map=column_map)
