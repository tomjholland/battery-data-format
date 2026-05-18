"""bdf2 — schema-driven battery cycler file ingestion.

Public surface:

- :class:`bdf2.schema.Source` — source identity + normalizer + metadata parser
- :class:`bdf2.schema.Normalizer` — column-mapping model with named fields per BDF mr_name
- :class:`bdf2.schema.MetadataParser` — fixed-field metadata extraction model
- :class:`bdf2.readers.BaseReader` subclasses (:class:`CSVReader`, :class:`ExcelReader`,
  :class:`MATReader`) — parse → normalize pipeline per file family
- :func:`bdf2.read` — dispatch by extension
- :func:`bdf2.normalize` — apply a Source/Normalizer to an existing DataFrame
"""

from ._normalize import normalize
from ._read import read
from .readers import BaseReader, CSVReader, ExcelReader, MATReader
from .schema import (
    DateTimeSyn,
    MetadataParser,
    Normalizer,
    ResolvedColumn,
    Source,
    Syn,
    SynUnion,
)
from .sources import REGISTRY, get_normalizer


__version__ = "0.2.0"
__all__ = [
    "BaseReader",
    "CSVReader",
    "DateTimeSyn",
    "ExcelReader",
    "MATReader",
    "MetadataParser",
    "Normalizer",
    "REGISTRY",
    "ResolvedColumn",
    "Source",
    "Syn",
    "SynUnion",
    "get_normalizer",
    "normalize",
    "read",
]
