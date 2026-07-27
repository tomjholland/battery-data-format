"""Field-declared JSON-LD projection markers with rdflib encode/decode.

Each projected field carries an ``Annotated[type, Marker(...)]`` marker. A
marker projects its value into an rdflib graph (:meth:`RdfMarker.encode`) and
reads it back (:meth:`RdfMarker.decode`). A whole model round-trips through the
symmetric pair :func:`to_graph` / :func:`from_graph`, whose walker is a uniform
per-field loop, so adding a projected field never edits a serialiser. A field
carrying no marker is not projected at all.

Behavioural bases:

* :class:`FlatMarker` — ``subject --term--> Literal``; the coercion is realised
  by the marker kind.
* :class:`ValueNodeMarker` — ``subject --edge--> node`` where the node carries a
  ``@type`` and holds a single value under a value predicate, discriminated by
  the triples the marker declares in :meth:`~ValueNodeMarker._identity`.
* :class:`SubModelMarker` — ``subject --edge--> node`` where the node is a whole
  nested model, projected field-by-field through :func:`to_graph`, so a nested
  model's node is built from its own field markers rather than hand-rolled here.

Two rules hold throughout and are enforced by tests rather than convention:

* **No RDF collections.** Repeated values are repeated direct edges, never an
  ``rdf:List`` head. Order is therefore not asserted in RDF, and decode returns
  repeated values in a deterministic sorted order.
* **No IRI is built from a label.** :data:`_PREFIXES` carries only namespaces
  whose local names BDF governs or that are stable W3C terms. Every EMMO,
  BattINFO, electrochemistry, and CHAMEO term is resolved by key through
  :mod:`bdf.vocabulary`, which fails closed on an unknown key.

Round-trip equivalence is compared as RDF, never as bytes. That comparison is a
property of the tests rather than of the format surface, so it lives in the test
tree (``tests/unit/_rdf_equivalence.py``) and not here.
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from .vocabulary import (
    BATTINFO_CONTEXT_URL,
    BDF,
    PROV,
    SCHEMA,
    UnknownTermError,
    battinfo_context,
    resolve,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

#: A node a model's triples may hang from. Written documents always name their
#: subject; a blank node is reachable only for an in-memory graph.
Subject = Union[URIRef, BNode]

# ── Namespaces + CURIE resolution ─────────────────────────────────────────────

#: Namespaces a CURIE may resolve against by plain concatenation.
#:
#: Deliberately restricted to namespaces whose local names BDF governs (``bdf``)
#: or that are stable W3C terms (``schema``, ``prov``, ``xsd``, ``rdf``,
#: ``rdfs``). There is no ``emmo`` or ``battinfo`` entry: an EMMO term has
#: exactly one route into the projection — :func:`bdf.vocabulary.resolve` — and
#: that route fails closed. Concatenating a prefix with a guessed local name is
#: how ``emmo:hasNumericalData``, a term that does not exist, was emitted for
#: months without any test noticing.
_PREFIXES: dict[str, Namespace] = {
    "schema": SCHEMA,
    "prov": PROV,
    "bdf": BDF,
    "xsd": Namespace(str(XSD)),
    "rdfs": Namespace(str(RDFS)),
    "rdf": Namespace(str(RDF)),
}

# The DOI scheme IRI, used as the propertyID of a DOI identifier node.
_DOI_PROPERTY_ID = URIRef("https://doi.org/")


def _uri(curie_or_iri: str) -> URIRef:
    """Resolve a CURIE (``prefix:local``) or absolute IRI to a ``URIRef``.

    Args:
        curie_or_iri: A ``prefix:local`` CURIE or an absolute IRI.

    Returns:
        The resolved ``URIRef``.

    Raises:
        ValueError: If the prefix is unknown and the value is not an IRI. An
            ``emmo:``/``battinfo:`` CURIE is unresolvable here by design; use
            :func:`bdf.vocabulary.resolve` for those terms.
    """
    if "://" in curie_or_iri:
        return URIRef(curie_or_iri)
    prefix, sep, local = curie_or_iri.partition(":")
    if not sep or prefix not in _PREFIXES:
        raise ValueError(f"unresolvable CURIE: {curie_or_iri!r}")
    return _PREFIXES[prefix][local]


def bind_prefixes(graph: Graph) -> None:
    """Bind the projection prefixes onto a graph for readable serialisation.

    Args:
        graph: Graph to bind the ``schema``/``prov``/``bdf``/… prefixes onto.
    """
    for prefix, ns in _PREFIXES.items():
        graph.bind(prefix, ns)


def _node_sort_key(node: Any) -> tuple[str, int]:
    """Return a deterministic sort key for a repeated-edge object.

    Repeated nested nodes are minted with a trailing ``-<n>`` index, so the
    emission order is recoverable from the node IRIs alone without an
    ``rdf:List``.

    Args:
        node: A graph term (node IRI or literal).

    Returns:
        ``(stem, index)``; ``index`` is ``-1`` when the term carries no numeric
        suffix, so unindexed terms sort by their text alone.
    """
    text = str(node)
    stem, sep, tail = text.rpartition("-")
    if sep and tail.isdigit():
        return (stem, int(tail))
    return (text, -1)


def _ordered_objects(graph: Graph, subject: Any, predicate: URIRef) -> list[Any]:
    """Return the objects of ``predicate`` in a deterministic order.

    Args:
        graph: Source graph.
        subject: Subject the edges hang from.
        predicate: Repeated edge predicate.

    Returns:
        The objects, sorted by :func:`_node_sort_key`.
    """
    return sorted(graph.objects(subject, predicate), key=_node_sort_key)


# ── Encode/decode context ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ctx:
    """Per-field context the walker passes to marker encode/decode.

    Attributes:
        field_name: Model field name, used to key term lookups and to build the
            field-relative identifiers of nested value nodes.
        nested_cls: For sub-model fields, the resolved nested model class (the
            model inside ``list[Model]``/``Model | None``); ``None`` otherwise.
        is_list: Whether the field's annotation wraps a ``list``. Since repeated
            values are repeated direct edges rather than an ``rdf:List`` head,
            the graph no longer carries the list/single distinction and decode
            reads it from the declaration instead.
    """

    field_name: str = ""
    nested_cls: type[BaseModel] | None = None
    is_list: bool = False


# ── Marker base ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RdfMarker:
    """Base for all projection markers."""

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Add the triples projecting ``value`` under ``subject`` to ``graph``.

        Args:
            graph: Target graph.
            subject: Subject the field's triples hang from.
            value: Python field value.
            ctx: Per-field context.
        """
        raise NotImplementedError

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Read this field's value back from ``graph``.

        Args:
            graph: Source graph.
            subject: Subject the field's triples hang from.
            ctx: Per-field context.

        Returns:
            The reconstructed Python value, or ``None`` when absent.
        """
        raise NotImplementedError


# ── Flat markers ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FlatMarker(RdfMarker):
    """``subject --term--> Literal``; coercion realised by the marker kind.

    Attributes:
        term: Predicate CURIE/IRI this field maps to (e.g. ``"schema:name"``).
    """

    term: str

    def _object(self, value: Any) -> Any:
        """Return the graph object (literal/IRI) for ``value``.

        Args:
            value: Python field value.

        Returns:
            The rdflib term to store under ``term``.
        """
        return Literal(value)

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Add ``(subject, term, object(value))`` unless ``value`` is ``None``.

        Args:
            graph: Target graph.
            subject: Field subject.
            value: Python field value.
            ctx: Per-field context (unused).
        """
        if value is None:
            return
        graph.add((subject, _uri(self.term), self._object(value)))

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Read the single object stored under ``term``.

        Args:
            graph: Source graph.
            subject: Field subject.
            ctx: Per-field context (unused).

        Returns:
            The Python value (``toPython`` handles literals and IRIs alike), or
            ``None`` when absent.
        """
        obj = graph.value(subject, _uri(self.term))
        return obj.toPython() if isinstance(obj, (Literal, URIRef)) else None


@dataclass(frozen=True)
class Scalar(FlatMarker):
    """Plain literal under ``term``."""


@dataclass(frozen=True)
class Typed(FlatMarker):
    """Typed literal; ``datatype`` is the ``@type`` datatype IRI.

    Attributes:
        datatype: Datatype CURIE (e.g. ``"xsd:date"``).
    """

    datatype: str = "xsd:string"

    def _object(self, value: Any) -> Any:
        """Return a datatyped literal for ``value``.

        Args:
            value: Python field value (``date``/``datetime``/``bool``/…).

        Returns:
            A ``Literal`` carrying ``datatype``.
        """
        return Literal(value, datatype=_uri(self.datatype))


@dataclass(frozen=True)
class Ref(FlatMarker):
    """IRI reference; the value is stored as a ``URIRef`` (decoded to a string)."""

    def _object(self, value: Any) -> Any:
        """Return a ``URIRef`` for ``value``.

        Args:
            value: URL-like field value.

        Returns:
            A ``URIRef`` (whose ``toPython`` yields the IRI string).
        """
        return URIRef(str(value))


@dataclass(frozen=True)
class StrList(FlatMarker):
    """Repeated literals under ``term``, one direct edge each.

    An ``rdf:List`` head would assert an order the data does not have and would
    put a blank collection node into every document. Order is consequently not
    preserved: :meth:`decode` returns the values sorted, so the round-trip is
    stable but a caller's original ordering is not a contract.
    """

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Add one direct edge per item under ``term``.

        Args:
            graph: Target graph.
            subject: Field subject.
            value: List of items (empty/``None`` emits nothing).
            ctx: Per-field context (unused).
        """
        if not value:
            return
        predicate = _uri(self.term)
        for item in value:
            graph.add((subject, predicate, Literal(item)))

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Read the repeated literals stored under ``term``.

        Args:
            graph: Source graph.
            subject: Field subject.
            ctx: Per-field context (unused).

        Returns:
            The sorted list of Python values, or ``None`` when absent.
        """
        items = sorted(
            obj.toPython() for obj in graph.objects(subject, _uri(self.term)) if isinstance(obj, (Literal, URIRef))
        )
        return items or None


# ── Type-selector marker ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RdfType(RdfMarker):
    """Projects a field's value onto the node's ``@type`` via a prefix.

    E.g. ``RdfType(prefix="schema:")`` maps ``"Person"`` ⇄ ``schema:Person``, so
    a discriminator field drives the node type declaratively instead of a
    bespoke branch. Only the governed prefixes of :data:`_PREFIXES` are
    available, so this cannot reach an EMMO class.

    Attributes:
        prefix: The CURIE prefix (with trailing colon) the value is appended to.
    """

    prefix: str = "schema:"

    def _namespace(self) -> str:
        """Return the resolved namespace IRI string for ``prefix``.

        Returns:
            The absolute namespace IRI (e.g. ``"https://schema.org/"``).
        """
        return str(_PREFIXES[self.prefix.rstrip(":")])

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Add ``(subject, rdf:type, <prefix+value>)``.

        Args:
            graph: Target graph.
            subject: The node whose ``@type`` is set.
            value: Local type name (``None`` emits nothing).
            ctx: Per-field context (unused).
        """
        if value is None:
            return
        graph.add((subject, RDF.type, _uri(f"{self.prefix}{value}")))

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Return the local name of the ``@type`` that sits in ``prefix``'s namespace.

        Args:
            graph: Source graph.
            subject: The node whose ``@type`` is read.
            ctx: Per-field context (unused).

        Returns:
            The local type name (e.g. ``"Person"``), or ``None`` when absent.
            Ties are broken by sort order so the result never depends on the
            graph's iteration order.
        """
        namespace = self._namespace()
        locals_ = sorted(
            str(node_type)[len(namespace) :]
            for node_type in graph.objects(subject, RDF.type)
            if str(node_type).startswith(namespace)
        )
        return locals_[0] if locals_ else None


# ── Value-node markers ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValueNodeMarker(RdfMarker):
    """``subject --edge--> [@type node with a value + identity triples]``.

    Subclasses set the ``edge`` / ``node_type`` / ``value_predicate`` constants,
    declare their discriminating triples in :meth:`_identity` (written on encode,
    matched on decode), and add non-identifying decorations in :meth:`_decorations`.
    """

    edge: ClassVar[URIRef] = SCHEMA.additionalProperty
    node_type: ClassVar[URIRef | None] = None
    value_predicate: ClassVar[URIRef] = SCHEMA.value

    def _edge(self, ctx: Ctx) -> URIRef:
        """Return the predicate linking the subject to the value node.

        Args:
            ctx: Per-field context.

        Returns:
            The edge ``URIRef``.
        """
        return self.edge

    def _type(self, ctx: Ctx) -> URIRef | None:
        """Return the node's ``@type``, or ``None`` for an untyped node.

        Args:
            ctx: Per-field context.

        Returns:
            The type ``URIRef`` or ``None``.
        """
        return self.node_type

    def _identity(self, ctx: Ctx) -> list[tuple[URIRef, URIRef]]:
        """Return the triples that uniquely identify this marker's node.

        Written verbatim on encode and matched on decode, so the discriminator
        is declared exactly once.

        Args:
            ctx: Per-field context.

        Returns:
            A list of ``(predicate, object)`` pairs (empty when the ``@type``
            and edge already discriminate the node).
        """
        return []

    def _node_id(self, subject: Subject, ctx: Ctx) -> URIRef | None:
        """Return a stable value-node IRI when the parent is named.

        Args:
            subject: Parent subject.
            ctx: Per-field context.

        Returns:
            A field-relative node IRI, or ``None`` for a blank parent.
        """
        if not isinstance(subject, URIRef) or not ctx.field_name:
            return None
        return URIRef(f"{subject}/{ctx.field_name}")

    def _decorations(self, graph: Graph, node: BNode | URIRef, value: Any, ctx: Ctx) -> None:
        """Add non-identifying decoration triples (unit, scheme, …).

        Args:
            graph: Target graph.
            node: The value node being built.
            value: Python field value.
            ctx: Per-field context.
        """
        return None

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Build the value node and link it under the edge.

        Args:
            graph: Target graph.
            subject: Field subject.
            value: Python field value (``None`` emits nothing).
            ctx: Per-field context.
        """
        if value is None:
            return
        node = self._node_id(subject, ctx) or BNode()
        node_type = self._type(ctx)
        if node_type is not None:
            graph.add((node, RDF.type, node_type))
        graph.add((node, self.value_predicate, Literal(value)))
        for predicate, obj in self._identity(ctx):
            graph.add((node, predicate, obj))
        self._decorations(graph, node, value, ctx)
        graph.add((subject, self._edge(ctx), node))

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Find the node matching this marker's ``@type`` + identity and read its value.

        Args:
            graph: Source graph.
            subject: Field subject.
            ctx: Per-field context.

        Returns:
            The Python value, or ``None`` when no matching node exists.
        """
        node_type = self._type(ctx)
        identity = self._identity(ctx)
        for node in _ordered_objects(graph, subject, self._edge(ctx)):
            if node_type is not None and (node, RDF.type, node_type) not in graph:
                continue
            if all((node, predicate, obj) in graph for predicate, obj in identity):
                obj = graph.value(node, self.value_predicate)
                if isinstance(obj, (Literal, URIRef)):
                    return obj.toPython()
        return None


@dataclass(frozen=True)
class OrgNode(ValueNodeMarker):
    """Embedded ``schema:Organization`` node built from a bare name string.

    Attributes:
        term: Edge predicate linking the subject to the organisation node.
    """

    term: str = "schema:manufacturer"
    node_type: ClassVar[URIRef] = SCHEMA.Organization
    value_predicate: ClassVar[URIRef] = SCHEMA.name

    def _edge(self, ctx: Ctx) -> URIRef:
        """Return the per-field edge predicate.

        Args:
            ctx: Per-field context.

        Returns:
            The edge ``URIRef`` resolved from ``term``.
        """
        return _uri(self.term)


@dataclass(frozen=True)
class IdentifierNode(ValueNodeMarker):
    """DOI (or other identifier) as a ``schema:PropertyValue``.

    Named ``IdentifierNode`` rather than ``Identifier`` so it does not shadow
    :class:`rdflib.term.Identifier` when both are in scope.
    """

    edge: ClassVar[URIRef] = SCHEMA.identifier
    node_type: ClassVar[URIRef] = SCHEMA.PropertyValue

    def _identity(self, ctx: Ctx) -> list[tuple[URIRef, URIRef]]:
        """Return the fixed DOI ``schema:propertyID`` discriminator.

        Args:
            ctx: Per-field context.

        Returns:
            A single ``(schema:propertyID, doi.org)`` pair.
        """
        return [(SCHEMA.propertyID, _DOI_PROPERTY_ID)]


def resolve_technique(value: str) -> URIRef | None:
    """Resolve a measurement-technique name to a CHAMEO class, or ``None``.

    CHAMEO publishes the battery characterisation-technique taxonomy
    (``ElectrochemicalImpedanceSpectroscopy``, ``HPPC``, ``CyclicVoltammetry``,
    ``OpenCircuitHold``, ``ICI``, and around fifty more). A name that does not
    resolve is not guessed at: the caller falls back to a text value, which
    ``schema:measurementTechnique`` permits.

    Args:
        value: A technique name, matched against the BattINFO context both
            verbatim and with separators and casing normalised away.

    Returns:
        The resolved class ``URIRef``, or ``None`` when no key matches.
    """
    candidates = [value, value.replace(" ", "").replace("-", "").replace("_", "")]
    for candidate in candidates:
        try:
            return resolve(candidate)
        except UnknownTermError:
            continue

    # Case-insensitive fall-back over the same normalised form, so "eis" and
    # "hppc" reach the published acronym classes.
    folded = candidates[-1].casefold()
    for key in battinfo_context():
        if key.casefold() == folded:
            return resolve(key)
    return None


@dataclass(frozen=True)
class DefinedTerm(ValueNodeMarker):
    """``schema:measurementTechnique`` resolved to a published CHAMEO class.

    When the value resolves, the node is additionally typed with the
    ``chameo:CharacterisationTechnique`` subclass — the resolved class *is* the
    identity. An unresolvable value degrades to a plain ``schema:DefinedTerm``
    carrying only its name, which is what ``schema:measurementTechnique``
    permits and what keeps an unpublished technique writable.
    """

    edge: ClassVar[URIRef] = SCHEMA.measurementTechnique
    node_type: ClassVar[URIRef] = SCHEMA.DefinedTerm
    value_predicate: ClassVar[URIRef] = SCHEMA.name

    def _decorations(self, graph: Graph, node: BNode | URIRef, value: Any, ctx: Ctx) -> None:
        """Type the node with its resolved CHAMEO class when one exists.

        Args:
            graph: Target graph.
            node: The DefinedTerm node.
            value: Python field value.
            ctx: Per-field context.
        """
        if value is None:
            return
        resolved = resolve_technique(str(value))
        if resolved is not None:
            graph.add((node, RDF.type, resolved))


# ── Reference marker ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SameAs(RdfMarker):
    """``schema:sameAs`` @id built from a constant prefix and the field value.

    Attributes:
        prefix: URL prefix prepended to the bare identifier.
    """

    prefix: str = ""
    edge: ClassVar[URIRef] = SCHEMA.sameAs

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Add ``(subject, schema:sameAs, <prefix+value>)``.

        Args:
            graph: Target graph.
            subject: Field subject.
            value: Bare identifier (``None`` emits nothing).
            ctx: Per-field context (unused).
        """
        if value is None:
            return
        graph.add((subject, self.edge, URIRef(f"{self.prefix}{value}")))

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Return the bare identifier of the ``sameAs`` ref matching ``prefix``.

        Args:
            graph: Source graph.
            subject: Field subject.
            ctx: Per-field context (unused).

        Returns:
            The prefix-stripped identifier, or ``None`` when none matches.
        """
        for obj in _ordered_objects(graph, subject, self.edge):
            text = str(obj)
            if text.startswith(self.prefix):
                return text[len(self.prefix) :].rstrip("/")
        return None


# ── Sub-model markers ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SubModelMarker(RdfMarker):
    """Projects a nested model as a node, field-by-field via the walker.

    Handles the single/list plumbing once; subclasses only decide the edge
    (:meth:`_edge`) and how one node is built (:meth:`_project_node`). A list
    field emits one direct edge per member rather than an ``rdf:List`` head, so
    decode reads ``ctx.is_list`` to know whether to return one value or many,
    and ``ctx.nested_cls`` to reconstruct each node's fields.
    """

    def _edge(self) -> URIRef:
        """Return the predicate linking the subject to the node.

        Returns:
            The edge ``URIRef``.
        """
        raise NotImplementedError

    def _project_node(self, graph: Graph, subject: Subject, value: Any, index: int | None = None) -> Subject:
        """Build one node for ``value`` and return its identifier.

        Args:
            graph: Target graph.
            subject: Parent subject (for promoted node ids).
            value: One nested-model instance.
            index: Position within a list field, or ``None`` for a single value;
                subclasses minting named nodes use it to keep ids distinct.

        Returns:
            The node identifier (blank or named).
        """
        raise NotImplementedError

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Project a single value, or one direct edge per list member.

        Args:
            graph: Target graph.
            subject: Field subject.
            value: A nested model, a list of them, or ``None``.
            ctx: Per-field context (unused).
        """
        if value is None:
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                graph.add((subject, self._edge(), self._project_node(graph, subject, item, index)))
        else:
            graph.add((subject, self._edge(), self._project_node(graph, subject, value)))

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Reconstruct the nested model's kwargs (dict, or list of dicts).

        Args:
            graph: Source graph.
            subject: Field subject.
            ctx: Per-field context carrying ``nested_cls`` and ``is_list``.

        Returns:
            A kwargs dict, a list of kwargs dicts, or ``None`` when absent. The
            dicts are validated into models when the parent model is built.
        """
        if ctx.nested_cls is None:
            return None
        nodes = _ordered_objects(graph, subject, self._edge())
        if not nodes:
            return None
        if ctx.is_list:
            return [_decode_fields(graph, node, ctx.nested_cls) for node in nodes]
        kwargs = _decode_fields(graph, nodes[0], ctx.nested_cls)
        return kwargs or None


@dataclass(frozen=True)
class NodeRef(SubModelMarker):
    """Promotes a sub-model to its own named graph node, linked from the subject.

    The target may equally be a reference string: a ``str`` is an IRI, absolute
    or document-relative, and produces the same RDF edge as an inline model.

    Attributes:
        role: Node role; also the ``bdf:`` edge local name and ``#`` fragment.
        term: Explicit edge predicate CURIE/IRI, overriding ``bdf:<role>``.
    """

    role: str = ""
    term: str | None = None

    def _edge(self) -> URIRef:
        """Return the edge predicate.

        Returns:
            The resolved ``term``, or ``bdf:<role>`` when no term is declared.
        """
        return _uri(self.term) if self.term is not None else BDF[self.role]

    def _project_node(self, graph: Graph, subject: Subject, value: Any, index: int | None = None) -> URIRef:
        """Promote ``value`` to a named node and project its fields.

        Args:
            graph: Target graph.
            subject: Parent subject.
            value: The sub-model instance.
            index: List position; appended to the fragment so the members of a
                list field get distinct ids instead of collapsing onto one node.

        Returns:
            The promoted node identifier: ``<subject>#<role>`` when the parent
            has no fragment of its own, else ``<subject>/<role>``, suffixed
            ``-<n>`` for the ``n``-th member of a list. An IRI carries at most
            one ``#``; ``/`` is legal inside a fragment, which is what keeps a
            nested identifier both well-formed and readable.
        """
        role = self.role or (self.term.rsplit(":", 1)[-1] if self.term else "node")
        fragment = role if index is None else f"{role}-{index + 1}"
        separator = "/" if "#" in str(subject) else "#"
        node = URIRef(f"{subject}{separator}{fragment}")
        to_graph(value, graph, node, rdf_type=getattr(value, "_rdf_type", None))
        return node

    def encode(self, graph: Graph, subject: Subject, value: Any, ctx: Ctx) -> None:
        """Project model targets inline or emit an external IRI reference.

        Args:
            graph: Target graph.
            subject: Parent subject.
            value: Nested model, reference IRI string, or a list of either.
            ctx: Per-field context.
        """
        if value is None:
            return
        if isinstance(value, (str, URIRef)):
            graph.add((subject, self._edge(), URIRef(str(value))))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, (str, URIRef)):
                    graph.add((subject, self._edge(), URIRef(str(item))))
                else:
                    graph.add((subject, self._edge(), self._project_node(graph, subject, item, index)))
            return
        super().encode(graph, subject, value, ctx)

    def decode(self, graph: Graph, subject: Subject, ctx: Ctx) -> Any:
        """Decode inline target nodes, or preserve unresolved reference IRIs.

        Args:
            graph: Source graph.
            subject: Parent subject.
            ctx: Per-field context carrying ``nested_cls`` and ``is_list``.

        Returns:
            Nested model kwargs when the target's fields are present in the
            graph, otherwise the reference IRI string.
        """
        nodes = _ordered_objects(graph, subject, self._edge())
        if not nodes:
            return None
        if ctx.is_list:
            return [self._decode_one(graph, node, ctx) for node in nodes]
        return self._decode_one(graph, nodes[0], ctx)

    def _decode_one(self, graph: Graph, node: Any, ctx: Ctx) -> Any:
        """Decode one target node into kwargs, or return its IRI.

        Args:
            graph: Source graph.
            node: The target node.
            ctx: Per-field context carrying ``nested_cls``.

        Returns:
            A kwargs dict when the node carries triples of its own and the
            nested class is known, otherwise the node's IRI string.
        """
        if ctx.nested_cls is not None and any(graph.predicate_objects(node)):
            return _decode_fields(graph, node, ctx.nested_cls)
        return str(node)


# ── Marker accessor + symmetric walker ────────────────────────────────────────


def _rdf_of(field_info: FieldInfo) -> RdfMarker | None:
    """Return the first ``RdfMarker`` in a pydantic field's metadata.

    Args:
        field_info: Pydantic ``FieldInfo`` for the field under inspection.

    Returns:
        The first ``RdfMarker`` instance, or ``None`` when the field is
        unmarked (not projected to JSON-LD).
    """
    for item in field_info.metadata:
        if isinstance(item, RdfMarker):
            return item
    return None


def _nested_model(field_info: FieldInfo) -> type[BaseModel] | None:
    """Return the nested pydantic model class wrapped by a field annotation.

    Digs through ``Optional[...]`` and ``list[...]`` wrappers.

    Args:
        field_info: Pydantic ``FieldInfo`` whose annotation may wrap a model.

    Returns:
        The model class, or ``None`` when the annotation wraps no model.
    """
    stack = [field_info.annotation]
    while stack:
        current = stack.pop()
        if get_origin(current) in (Union, types.UnionType, list):
            stack.extend(get_args(current))
            continue
        if isinstance(current, type) and issubclass(current, BaseModel):
            return current
    return None


def _is_list_field(field_info: FieldInfo) -> bool:
    """Report whether a field's annotation wraps a ``list``.

    Args:
        field_info: Pydantic ``FieldInfo`` for the field.

    Returns:
        True when ``list[...]`` appears anywhere in the annotation, including
        inside an ``Optional``/union wrapper.
    """
    stack = [field_info.annotation]
    while stack:
        current = stack.pop()
        origin = get_origin(current)
        if origin is list:
            return True
        if origin in (Union, types.UnionType):
            stack.extend(get_args(current))
    return False


def _ctx_for(name: str, field_info: FieldInfo) -> Ctx:
    """Build the per-field context for a model field.

    Args:
        name: Field name.
        field_info: Pydantic ``FieldInfo`` for the field.

    Returns:
        A ``Ctx`` carrying the field name, resolved nested model class, and
        whether the field is list-valued.
    """
    return Ctx(
        field_name=name,
        nested_cls=_nested_model(field_info),
        is_list=_is_list_field(field_info),
    )


def to_graph(model: Any, graph: Graph, subject: Subject, *, rdf_type: str | None = None) -> Graph:
    """Project every marked field of ``model`` onto ``subject`` in ``graph``.

    Args:
        model: Pydantic model instance to project.
        graph: Target graph.
        subject: Subject node for this model's triples.
        rdf_type: Optional ``@type`` CURIE/IRI to stamp on the subject. It is a
            fallback: a field-declared :class:`RdfType` owns the node's ``@type``,
            so the stamp is only added when the fields leave the node untyped.

    Returns:
        The same ``graph``, for chaining.
    """
    for name, field_info in type(model).model_fields.items():
        marker = _rdf_of(field_info)
        if marker is not None:
            marker.encode(graph, subject, getattr(model, name), _ctx_for(name, field_info))
    if rdf_type is not None and (subject, RDF.type, None) not in graph:
        graph.add((subject, RDF.type, _uri(rdf_type)))
    return graph


def _decode_fields(graph: Graph, subject: Subject, model_cls: type[BaseModel]) -> dict[str, Any]:
    """Decode every marked field of ``model_cls`` into a kwargs dict.

    The uniform per-field loop that both :func:`from_graph` and sub-model
    markers use; nested models come back as kwargs dicts and are validated into
    instances when the owning model is constructed.

    Args:
        graph: Source graph.
        subject: Subject node the model's triples hang from.
        model_cls: Pydantic model class to reconstruct.

    Returns:
        A dict of field-name → decoded value for every present marked field.
    """
    kwargs: dict[str, Any] = {}
    for name, field_info in model_cls.model_fields.items():
        marker = _rdf_of(field_info)
        if marker is None:
            continue
        value = marker.decode(graph, subject, _ctx_for(name, field_info))
        if value is not None:
            kwargs[name] = value
    return kwargs


def from_graph(model_cls: type[ModelT], graph: Graph, subject: Subject) -> ModelT:
    """Reconstruct a ``model_cls`` instance from its triples in ``graph``.

    The decode counterpart of :func:`to_graph`; nested sub-models are coerced by
    pydantic from the decoded kwargs dicts.

    Args:
        model_cls: Pydantic model class to reconstruct.
        graph: Source graph.
        subject: Subject node the model's triples hang from.

    Returns:
        A validated ``model_cls`` instance.
    """
    return model_cls(**_decode_fields(graph, subject, model_cls))


# ── BdfModel base ──────────────────────────────────────────────────────────────

BdfModelT = TypeVar("BdfModelT", bound="BdfModel")


def _root_subject(graph: Graph) -> Subject:
    """Return the one subject in ``graph`` that is never anyone's object.

    A model's own subject is the graph's root: every other node (value nodes,
    promoted :class:`NodeRef` nodes) is reached *from* it, so it is the only
    subject that never appears as an object.

    Args:
        graph: A graph produced by a single model's :meth:`BdfModel.to_graph`.

    Returns:
        The root subject.

    Raises:
        ValueError: If the graph has zero or more than one such subject.
    """
    subjects = {s for s in graph.subjects() if isinstance(s, (URIRef, BNode))}
    objects = {o for _, _, o in graph}
    roots = subjects - objects
    if len(roots) != 1:
        raise ValueError(f"cannot resolve a unique root subject: found {len(roots)} candidates")
    return roots.pop()


def document_subject(path: str | Path, *, fragment: str = "record") -> URIRef:
    """Return the root subject for a document written to ``path``.

    Identifiers are document-relative: the subject is the document's own file
    name plus a fragment, so a sidecar read from anywhere resolves its own
    subject and nothing depends on an absolute location. This is the reason no
    document ever has a blank root subject — there is always a name to derive
    one from.

    Args:
        path: The document's path; only its file name is used.
        fragment: The entity fragment after ``#``.

    Returns:
        The root subject ``URIRef`` (e.g. ``dataset.jsonld#record``).
    """
    return URIRef(f"{Path(path).name}#{fragment}")


def _as_jsonld_html(jsonld: str, *, title: str = "") -> str:
    """Embed a JSON-LD document in a minimal HTML page.

    Args:
        jsonld: Serialised JSON-LD text.
        title: Optional ``<title>`` text.

    Returns:
        A minimal HTML document embedding ``jsonld`` in a
        ``<script type="application/ld+json">`` tag.
    """
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{title}</title>
    <script type="application/ld+json">
{jsonld}
    </script>
  </head>
  <body></body>
</html>
"""


class BdfModel(BaseModel):
    """Shared base for every metadata model: config, ``to_graph``/``from_graph``/``write``/``load``.

    Every projected field already declares its whole JSON-LD mapping via an
    :class:`RdfMarker`, so this base carries the entire serialisation surface
    generically — no subclass writes any encode/decode/serialise code. See this
    module's docstring for the marker taxonomy.

    Attributes:
        _rdf_type: Optional node ``@type`` CURIE stamped by :meth:`to_graph`. A
            fallback: a field-declared :class:`RdfType` on the subclass still
            wins (see :func:`to_graph`'s ``rdf_type`` parameter).
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    _rdf_type: ClassVar[str | None] = None

    def to_graph(self, graph: Graph | None = None, subject: Subject | None = None) -> Graph:
        """Project this model onto a graph.

        Args:
            graph: Target graph; a fresh, prefix-bound graph is created when
                omitted.
            subject: Subject node for this model's triples; a blank node is
                minted when omitted. Written documents always supply one —
                :meth:`write` derives it from the document's own name — so a
                blank root subject never reaches a file.

        Returns:
            The graph the model was projected onto.
        """
        if graph is None:
            graph = Graph()
            bind_prefixes(graph)
        if subject is None:
            subject = BNode()
        to_graph(self, graph, subject, rdf_type=self._rdf_type)
        return graph

    @classmethod
    def from_graph(cls: type[BdfModelT], graph: Graph, subject: Subject) -> BdfModelT:
        """Reconstruct an instance from its triples in ``graph``.

        Args:
            graph: Source graph.
            subject: Subject node the model's triples hang from.

        Returns:
            A validated instance of this class.
        """
        return from_graph(cls, graph, subject)

    def write(self, path: str | Path, *, fmt: str | None = None) -> Path:
        """Serialise this model to ``path``, format inferred from its suffix.

        Args:
            path: Output path. Parent directories are created as needed.
            fmt: Explicit format (``"json"``/``"jsonld"``/``"html"``), overriding
                suffix inference.

        Returns:
            ``path``.

        Raises:
            ValueError: If the format cannot be determined or is unsupported.
        """
        path = Path(path)
        resolved_fmt = fmt or path.suffix.lstrip(".")
        path.parent.mkdir(parents=True, exist_ok=True)
        if resolved_fmt == "json":
            path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        elif resolved_fmt == "jsonld":
            path.write_text(to_compact_jsonld(self, path=path), encoding="utf-8")
        elif resolved_fmt == "html":
            jsonld = to_compact_jsonld(self, path=path)
            path.write_text(_as_jsonld_html(jsonld, title=type(self).__name__), encoding="utf-8")
        else:
            raise ValueError(f"unsupported format: {resolved_fmt!r}")
        return path

    @classmethod
    def load(cls: type[BdfModelT], path: str | Path) -> BdfModelT:
        """Load an instance previously written by :meth:`write`.

        Args:
            path: Input path; format is inferred from its suffix (``.json`` or
                ``.jsonld``).

        Returns:
            A validated instance of this class.

        Raises:
            ValueError: If the format cannot be determined or is unsupported.
        """
        path = Path(path)
        resolved_fmt = path.suffix.lstrip(".")
        if resolved_fmt == "json":
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        if resolved_fmt == "jsonld":
            return from_compact_jsonld(cls, path.read_text(encoding="utf-8"))
        raise ValueError(f"unsupported format: {resolved_fmt!r}")


# ── Compact JSON-LD (context-compaction, dependency-free) ────────────────────


def to_compact_jsonld(
    model: BaseModel,
    *,
    path: str | Path | None = None,
    base: str | None = None,
    subject: str | URIRef | None = None,
) -> str:
    """Serialise a model to compact, context-carrying JSON-LD.

    The root subject is never blank. It comes from ``subject`` when given, else
    from ``base``, else from the document's own file name via ``path`` — which
    is why writing a document needs no caller-supplied identity.

    Args:
        model: A pydantic model whose fields carry :class:`RdfMarker` metadata
            (typically a :class:`BdfModel`).
        path: The document's own path. Its file name becomes the document base,
            so identifiers are relative to the document that carries them.
        base: Explicit document base used to resolve relative IRIs, overriding
            the one derived from ``path``.
        subject: Explicit root subject, overriding the one derived from the base.

    Returns:
        JSON-LD text with a ``@context`` binding the projection's prefixes,
        compacted over a flat ``@graph``.

    Raises:
        ValueError: If none of ``subject``, ``base``, or ``path`` is supplied,
            since the root subject would otherwise be a blank node.
    """
    if base is None and path is not None:
        base = Path(path).name
    if subject is not None:
        root = URIRef(str(subject))
    elif base is not None:
        root = URIRef(f"{base}#record")
    else:
        raise ValueError("a document base, path, or explicit subject is required: the root subject is never blank")

    graph = Graph()
    bind_prefixes(graph)
    to_graph(model, graph, root, rdf_type=getattr(model, "_rdf_type", None))
    context = {prefix: str(namespace) for prefix, namespace in _PREFIXES.items()}
    if base is not None:
        context["@base"] = str(base)
    text = graph.serialize(format="json-ld", context=context, auto_compact=True)
    return _reference_battinfo_context(text)


def _reference_battinfo_context(text: str) -> str:
    """Prepend the published BattINFO context reference to a document's ``@context``.

    The inline prefix mapping already expands every term the projection emits,
    so the document stays self-sufficient offline. The upstream reference is
    added so BattINFO's own readable keys expand canonically for any consumer
    that resolves it.

    Args:
        text: Serialised JSON-LD document.

    Returns:
        The document with an array-valued ``@context``, or ``text`` unchanged if
        it has no object-valued ``@context`` to extend.
    """
    doc = json.loads(text)
    if not isinstance(doc, dict):
        return text
    inline = doc.get("@context")
    if not isinstance(inline, dict):
        return text
    doc["@context"] = [BATTINFO_CONTEXT_URL, inline]
    return json.dumps(doc, indent=2)


def from_compact_jsonld(model_cls: type[ModelT], text: str, *, subject: str | URIRef | None = None) -> ModelT:
    """Reconstruct a model from compact JSON-LD text produced by :func:`to_compact_jsonld`.

    Args:
        model_cls: Pydantic model class to reconstruct.
        text: JSON-LD text (compact or expanded; any valid JSON-LD parses).
        subject: Optional explicit subject to decode.

    Returns:
        A validated ``model_cls`` instance.
    """
    graph = Graph()
    graph.parse(data=_inline_battinfo_context(text), format="json-ld")
    root = _root_subject(graph) if subject is None else URIRef(str(subject))
    return from_graph(model_cls, graph, root)


def _inline_battinfo_context(text: str) -> str:
    """Substitute the vendored context for its URL reference before parsing.

    Documents are emitted referencing the published context by URL, but reading
    one must never depend on network access. Any reference to the pinned context
    is swapped for the vendored copy; every other entry is left untouched.

    Args:
        text: JSON-LD document text.

    Returns:
        The document with the pinned context reference replaced by its vendored
        content, or ``text`` unchanged when it carries no such reference.
    """
    try:
        doc = json.loads(text)
    except ValueError:
        return text
    if not isinstance(doc, dict):
        return text
    context = doc.get("@context")
    if not isinstance(context, list) or BATTINFO_CONTEXT_URL not in context:
        return text
    vendored = battinfo_context()
    doc["@context"] = [vendored if entry == BATTINFO_CONTEXT_URL else entry for entry in context]
    return json.dumps(doc)
