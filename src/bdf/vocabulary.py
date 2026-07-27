"""Key-to-IRI resolution against BattINFO's published JSON-LD context.

No IRI is ever built from a label. Every EMMO/BattINFO/CHAMEO class and
predicate the projection emits is resolved by *key* against the vendored,
version-pinned copy of BattINFO's own ``context/context.json``
(:data:`BATTINFO_CONTEXT_PATH`), and an unresolvable key raises
:class:`UnknownTermError` rather than producing a guessed IRI.

The same vendored file serves three purposes: resolution (a field names
``UpperVoltageLimit``, the context supplies the IRI), emission (the context is
referenced from every emitted document so readable keys expand canonically),
and validation (a key absent from the context is rejected before writing).

Terms that are *or could be* a BDF data column resolve through
``spec.COLUMN_ONTOLOGY`` instead; those carry their own EMMO and QUDT mappings.
This module covers everything that is not a column.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
from pathlib import Path

from rdflib import Namespace, URIRef

# ── Pins ──────────────────────────────────────────────────────────────────────

#: BattINFO ``domain-battery`` release the vendored context is taken from.
BATTINFO_VERSION = "0.18.6"

#: Upstream location of the vendored context, used by the refresh script.
BATTINFO_CONTEXT_URL = (
    f"https://raw.githubusercontent.com/emmo-repo/domain-battery/{BATTINFO_VERSION}/context/context.json"
)

#: The ontology import the BDF snapshot pins. The pin follows the shipped
#: snapshot rather than leading it: the snapshot's own ``owl:imports`` names
#: this exact IRI, so the two can be asserted equal and never silently drift.
BATTINFO_ONTOLOGY_IRI = f"https://w3id.org/emmo/domain/battery/{BATTINFO_VERSION}/battery"

# ── Namespaces ────────────────────────────────────────────────────────────────

#: The canonical BDF ontology namespace. Replaces the obsolete
#: ``https://w3id.org/bdf/``, which resolves to nothing.
BDF = Namespace("https://w3id.org/battery-data-alliance/ontology/battery-data-format#")

SCHEMA = Namespace("https://schema.org/")
PROV = Namespace("http://www.w3.org/ns/prov#")


class UnknownTermError(KeyError):
    """A key could not be resolved against the vendored BattINFO context."""


def _context_path() -> Path:
    ref = importlib.resources.files("bdf.data").joinpath("battinfo-context.json")
    return Path(str(ref))


#: Path to the vendored BattINFO JSON-LD context.
BATTINFO_CONTEXT_PATH = _context_path()


@functools.lru_cache(maxsize=1)
def battinfo_context() -> dict[str, object]:
    """Return the vendored BattINFO ``@context`` mapping.

    Returns:
        The raw term mapping: key to either an IRI string or a term definition
        object carrying ``@id`` and optionally ``@type``.

    Raises:
        RuntimeError: If the vendored file is missing or has no ``@context``.
    """
    path = _context_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - packaging failure
        raise RuntimeError(
            f"Vendored BattINFO context missing at {path}. Run scripts/refresh_vocabulary.py to restore it."
        ) from exc
    context = raw.get("@context")
    if not isinstance(context, dict):
        raise RuntimeError(f"{path} has no object-valued '@context'")
    return context


def _near_matches(key: str, limit: int = 3) -> list[str]:
    import difflib

    return difflib.get_close_matches(key, list(battinfo_context()), n=limit, cutoff=0.6)


def resolve(key: str) -> URIRef:
    """Resolve a BattINFO context key to its canonical IRI.

    Args:
        key: A readable term name as published in BattINFO's context, e.g.
            ``"BatteryCycler"``, ``"UpperVoltageLimit"``, ``"hasNumberValue"``.

    Returns:
        The canonical ``URIRef`` for the term.

    Raises:
        UnknownTermError: If the key is absent from the context, or its term
            definition carries no usable ``@id``. Failing closed is deliberate:
            a missing key must never fall back to a constructed IRI.
    """
    context = battinfo_context()
    try:
        entry = context[key]
    except KeyError:
        hint = ""
        near = _near_matches(key)
        if near:
            hint = f" Did you mean {', '.join(repr(n) for n in near)}?"
        raise UnknownTermError(
            f"{key!r} is not a term in the pinned BattINFO context ({BATTINFO_VERSION}).{hint}"
        ) from None

    if isinstance(entry, str):
        iri = entry
    elif isinstance(entry, dict):
        candidate = entry.get("@id")
        if not isinstance(candidate, str):
            raise UnknownTermError(f"{key!r} has a term definition with no string '@id'")
        iri = candidate
    else:
        raise UnknownTermError(f"{key!r} has an unusable term definition of type {type(entry).__name__}")

    if not iri.startswith(("http://", "https://")):
        raise UnknownTermError(f"{key!r} resolves to a non-absolute IRI {iri!r}")
    return URIRef(iri)


def is_reference_term(key: str) -> bool:
    """Report whether a context key is declared as an object property.

    BattINFO already sets ``"@type": "@id"`` on predicates whose object is a
    resource, so the projection need not restate that.

    Args:
        key: A readable term name.

    Returns:
        True if the term definition declares ``"@type": "@id"``.

    Raises:
        UnknownTermError: If the key is absent from the context.
    """
    resolve(key)
    entry = battinfo_context()[key]
    return isinstance(entry, dict) and entry.get("@type") == "@id"


def has_term(key: str) -> bool:
    """Report whether a key resolves, without raising.

    Args:
        key: A readable term name.

    Returns:
        True if the key resolves to an absolute IRI.
    """
    try:
        resolve(key)
    except UnknownTermError:
        return False
    return True
