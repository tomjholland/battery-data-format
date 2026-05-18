"""bdf2 — clean-slate, schema-driven battery cycler file ingestion.

The public surface centres on three pieces:

- :class:`bdf2.schema.BDFColumn` enumerates the canonical BDF columns.
- :class:`bdf2.schema.Normalizer` instances (one per source, declared in
  ``bdf2.sources``) describe vendor synonyms, magic strings, and metadata
  patterns. The :class:`Normalizer.resolve` method matches headers to BDF
  columns and produces :class:`bdf2.schema.ResolvedColumn` records carrying
  the source header, pint conversion factor, and optional datetime format.
- :class:`bdf2.readers.BaseReader` subclasses (:class:`CSVReader`,
  :class:`ExcelReader`, :class:`MATReader`) own the parse → resolve → normalize
  pipeline for one file family. Use :func:`bdf2.read` to dispatch by extension
  or pass an explicit reader instance.
"""

from ._normalize import normalize
from ._read import read
from .readers import BaseReader, CSVReader, ExcelReader, MATReader
from .schema import (
    BDFColumn,
    DateTimeSyn,
    FieldSpec,
    Normalizer,
    ResolvedColumn,
    Style,
    Syn,
    SynUnion,
    column,
)
from .sources import REGISTRY, get_normalizer

__version__ = "0.2.0"
__all__ = [
    "BDFColumn",
    "BaseReader",
    "CSVReader",
    "DateTimeSyn",
    "ExcelReader",
    "FieldSpec",
    "MATReader",
    "Normalizer",
    "REGISTRY",
    "ResolvedColumn",
    "Style",
    "Syn",
    "SynUnion",
    "column",
    "get_normalizer",
    "normalize",
    "read",
]
