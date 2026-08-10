"""Metadata source parsers: ``MetadataParser`` base class and concrete sources.

A metadata parser combines a *source* (where the metadata lives) with *extraction*
(how to pull BDF metadata fields out of it). Identification is :meth:`matches`,
extraction is :meth:`parse`; each subclass owns all of its own file I/O.

Sources are fully orthogonal to readers: a delimited-text file may carry its
metadata in a preamble (:class:`TxtPreambleParser`) while any file may have an
adjacent JSON sidecar (:class:`JsonSidecarParser`). To keep that orthogonality at
the import level too, **this module MUST NOT import from** :mod:`bdf.readers`; it
reads the bytes it needs through :func:`read_head` from :mod:`bdf.file_utils`.

:class:`MetadataRules` is the single source of truth for BDF metadata field names
(symmetric with :class:`~bdf.table_normalizers.TableNormalizer`'s mr_name fields). Frozen +
scalar/tuple values ⇒ every parser instance is hashable, so ``PLUGINS.metadata_parsers``
can be a ``frozenset``.

``started_at`` and ``ended_at`` store integer epoch seconds. :class:`TxtPreambleParser`
coerces the text its regexes matched with :func:`bdf._datetime_fmt.to_epoch_seconds`;
:class:`JsonSidecarParser` passes a real JSON integer through unchanged for those two
fields instead of stringifying it.

Every parser's :meth:`~MetadataParser.parse` returns a hand-written
:class:`~bdf.battinfo_records.TestSection` instance, not a bare dict, so a caller
gets the same typed staging record regardless of which parser produced it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Generic, Iterator, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._datetime_fmt import to_epoch_seconds
from .battinfo_records import TestSection
from .file_utils import read_head

T = TypeVar("T")

# Metadata fields that store integer epoch seconds rather than free text.
_DATETIME_FIELDS = ("started_at", "ended_at")


class MetadataRules(BaseModel, Generic[T]):
    """Generic frozen model declaring one field per supported BDF metadata field.

    ``T`` is the per-parser extraction-rule type (``str`` regex patterns for
    :class:`TxtPreambleParser`, ``tuple[str, ...]`` synonym keys for
    :class:`JsonSidecarParser`). The set of fields here is the single source of
    truth for BDF metadata field names. ``extra="forbid"`` rejects typos at
    construction; frozen + scalar/tuple values keep instances hashable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: T | None = None
    instrument_name: T | None = None
    started_at: T | None = None
    ended_at: T | None = None

    def __iter__(self) -> Iterator[tuple[str, T]]:  # type: ignore[override]
        """Yield ``(field_name, rule)`` for each set (non-None) field in declaration order.

        Yields:
            Tuples of (field_name, rule) for all non-None fields in declaration order.
        """
        for field_name in type(self).model_fields:
            val = getattr(self, field_name)
            if val is not None:
                yield field_name, val

    def extract(self, match_one: Callable[[T], str | None]) -> dict[str, str]:
        """Resolve each set field by applying ``match_one`` to its rule.

        Args:
            match_one: Callable mapping a field's rule to the extracted value, or
                None when the rule finds no value.

        Returns:
            Mapping of field name to extracted value for every field whose rule matched.
        """
        result: dict[str, str] = {}
        for field_name, rule in self:
            value = match_one(rule)
            if value is not None:
                result[field_name] = value
        return result


def _require_datetime_formats(rules: MetadataRules, datetime_formats: tuple[str, ...]) -> None:
    """Reject a parser that declares a datetime rule but no format to read it with.

    Coercion failure is silent by design at parse time: an unparseable preamble
    line must not fail an otherwise good read. That leaves construction as the
    only place a plugin author who forgot the formats can be told, rather than
    the field silently staying unset forever.

    Args:
        rules: The parser's per-field extraction rules.
        datetime_formats: The parser's declared candidate datetime formats.

    Raises:
        ValueError: If a datetime field has a rule but no formats are declared.
    """
    if datetime_formats:
        return
    declared = tuple(field_name for field_name, _ in rules if field_name in _DATETIME_FIELDS)
    if declared:
        raise ValueError(
            f"datetime_formats must be set when a rule is declared for {', '.join(declared)}; "
            "without it the extracted text can never be coerced to epoch seconds"
        )


def _coerce_preamble_datetimes(
    fields: dict[str, str], datetime_formats: tuple[str, ...], tz: str
) -> dict[str, str | int]:
    """Coerce matched text for the datetime fields of ``fields`` to integer epoch seconds.

    A field whose text no candidate format parses is dropped rather than kept as
    unparsed text, matching the "unset, not an error" policy of the rest of
    metadata extraction.

    Args:
        fields: Field name to matched text, as returned by ``MetadataRules.extract``.
        datetime_formats: Ordered candidate strptime formats.
        tz: IANA timezone applied to naive formats.

    Returns:
        A copy of ``fields`` with each datetime field replaced by its epoch
        seconds, or removed when no format parsed its text.
    """
    result: dict[str, str | int] = dict(fields)
    for field_name in _DATETIME_FIELDS:
        text = fields.get(field_name)
        if text is None:
            continue
        epoch, _ = to_epoch_seconds(text, datetime_formats, tz)
        if epoch is None:
            del result[field_name]
        else:
            result[field_name] = epoch
    return result


class MetadataParser(BaseModel):
    """Base / null metadata parser: never matches, extracts nothing.

    Subclasses override :meth:`matches` and :meth:`parse` and own all file I/O
    for their source type. Frozen so instances are hashable.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["base"] = "base"

    def matches(self, path: str | Path) -> bool:
        """Return whether this parser recognises ``path`` as its source. Base: never.

        Args:
            path: Local file path or URL to check.

        Returns:
            False for base class (override in subclasses).
        """
        return False

    def parse(self, path: str | Path, *, tz: str = "UTC") -> TestSection:
        """Extract BDF metadata fields from ``path``. Base: nothing.

        Args:
            path: Local file path or URL to parse.
            tz: IANA timezone applied to naive datetime formats. Unused by the
                base class, which extracts nothing.

        Returns:
            An empty TestSection for the base class (override in subclasses).
        """
        return TestSection()


class TxtPreambleParser(MetadataParser):
    """Reads metadata from the head bytes of the data file itself.

    ``magic`` tokens identify the format; ``encoding`` decodes the head bytes;
    ``regex_patterns`` holds one regex per set field whose ``group(1)`` is the
    extracted value. :meth:`parse` applies each regex over the decoded head
    lines (no separator / skip-rows sniffing), then coerces ``started_at`` and
    ``ended_at`` text to integer epoch seconds with ``datetime_formats``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["txt_preamble"] = "txt_preamble"  # type: ignore[assignment]
    magic: tuple[str | bytes, ...] = Field(
        default=(),
        description=(
            "Tokens that identify this format: str tokens are matched case-insensitively "
            "against decoded head text; bytes tokens are matched as raw byte substrings."
        ),
    )
    encoding: str = Field(default="utf-8", description="Codec used to decode head bytes before regex matching.")
    regex_patterns: MetadataRules[re.Pattern[str]] = Field(
        default_factory=lambda: MetadataRules[re.Pattern[str]](),
        description="Per-field compiled regex patterns; each pattern's group(1) is the extracted value.",
    )
    datetime_formats: tuple[str, ...] = Field(
        default=(),
        description=(
            "Ordered candidate strptime formats tried, in order, to coerce matched "
            "started_at/ended_at text to integer epoch seconds. Required when a rule "
            "is declared for either field."
        ),
    )

    @model_validator(mode="after")
    def _check_datetime_formats(self) -> "TxtPreambleParser":
        """Reject a started_at/ended_at rule declared without datetime_formats.

        Returns:
            The validated parser.
        """
        _require_datetime_formats(self.regex_patterns, self.datetime_formats)
        return self

    def matches(self, path: str | Path) -> bool:
        """Return True when any magic token is found in the file's head bytes.

        Args:
            path: Local file path or URL to check.

        Returns:
            True if any magic token appears in the file head.
        """
        if not self.magic:
            return False
        head = read_head(path)
        text = head.decode("utf-8", errors="replace").lower()
        for m in self.magic:
            if isinstance(m, bytes):
                if m in head:
                    return True
            elif m.lower() in text:
                return True
        return False

    def parse(self, path: str | Path, *, tz: str = "UTC") -> TestSection:
        """Decode the head with ``encoding`` and apply each regex; first match per field.

        Args:
            path: Local file path or URL to parse.
            tz: IANA timezone applied to naive matched started_at/ended_at text.

        Returns:
            A TestSection with each field set to its first regex match, and
            started_at/ended_at coerced to integer epoch seconds.
        """
        head = read_head(path)
        lines = head.decode(self.encoding, errors="replace").splitlines()

        def match_one(rx: re.Pattern[str]) -> str | None:
            for line in lines:
                m = rx.search(line)
                if m:
                    return m.group(1).strip()
            return None

        matched = self.regex_patterns.extract(match_one)
        return TestSection(**_coerce_preamble_datetimes(matched, self.datetime_formats, tz))


class JsonSidecarParser(MetadataParser):
    """Reads metadata from a JSON file adjacent to the data file (``path.with_suffix(".json")``).

    ``key_synonyms`` holds an ordered tuple of candidate JSON keys per set field;
    :meth:`parse` returns the value of the first synonym key present in the JSON.
    A real JSON integer matched for ``started_at``/``ended_at`` passes through
    unchanged, since it is already epoch seconds; any other value matched for
    those two fields leaves the field unset rather than being stringified, since
    :class:`~bdf.battinfo_records.TestSection` types them as ``int | None``.
    Every other field's matched value is stringified.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["json_sidecar"] = "json_sidecar"  # type: ignore[assignment]
    key_synonyms: MetadataRules[tuple[str, ...]] = Field(
        default_factory=lambda: MetadataRules[tuple[str, ...]](),
        description="Per-field ordered tuples of candidate JSON keys.",
    )

    def _sidecar(self, path: str | Path) -> Path:
        """Return the sidecar JSON path for a data file.

        Args:
            path: Local file path to the data file.

        Returns:
            Path to the .json sidecar file (same name, .json suffix).
        """
        return Path(path).with_suffix(".json")

    def matches(self, path: str | Path) -> bool:
        """Return True when the ``.json`` sidecar file exists.

        Args:
            path: Local file path to the data file.

        Returns:
            True if the .json sidecar file exists.
        """
        return self._sidecar(path).exists()

    def parse(self, path: str | Path, *, tz: str = "UTC") -> TestSection:
        """Load the sidecar JSON and resolve each set field's synonym keys (first match).

        Args:
            path: Local file path to the data file.
            tz: Unused; the sidecar has no text datetime formats to localise.

        Returns:
            A TestSection built from the sidecar JSON: a real integer for a
            matched started_at/ended_at key, that field left unset when the
            matched value is not a real integer, else the value stringified.
        """
        sidecar = self._sidecar(path)
        if not sidecar.exists():
            return TestSection()
        with open(sidecar, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return TestSection()

        result: dict[str, str | int] = {}
        for field_name, keys in self.key_synonyms:
            for key in keys:
                if key not in data:
                    continue
                raw = data[key]
                is_real_int = isinstance(raw, int) and not isinstance(raw, bool)
                if field_name in _DATETIME_FIELDS:
                    if is_real_int:
                        result[field_name] = raw
                else:
                    result[field_name] = str(raw)
                break
        return TestSection(**result)
