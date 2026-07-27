"""Projection-shape and round-trip tests for the field-declared RDF markers.

Round-tripping alone is symmetric enough to hide a dropped ``@type``, unit, or
discriminator, so the emitted triples are asserted directly too. Three
structural invariants are asserted over every graph the fixtures produce rather
than trusted to review: no ``rdf:List`` node, no blank root subject, and no
route by which an EMMO term could be written by prefix interpolation.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, ClassVar

import pytest
from pydantic import Field, ValidationError
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

from bdf.metadata_projection import (
    _PREFIXES,
    BDF,
    SCHEMA,
    BdfModel,
    Ctx,
    DefinedTerm,
    IdentifierNode,
    NodeRef,
    OrgNode,
    RdfType,
    Ref,
    SameAs,
    Scalar,
    StrList,
    Typed,
    _uri,
    bind_prefixes,
    document_subject,
    from_compact_jsonld,
    to_compact_jsonld,
)
from bdf.vocabulary import resolve
from unit._rdf_equivalence import canonical_nquads, graphs_equivalent

_SUBJECT = URIRef("doc.jsonld#record")


# ── Fixture models ────────────────────────────────────────────────────────────


class Org(BdfModel):
    """An organisation node, exercising the sub-model and ``sameAs`` markers."""

    _rdf_type: ClassVar[str | None] = "schema:Organization"

    name: Annotated[str, Scalar("schema:name")]
    ror: Annotated[str | None, SameAs(prefix="https://ror.org/")] = None


class Record(BdfModel):
    """A model carrying one field of every ported marker, plus an unmarked one."""

    _rdf_type: ClassVar[str | None] = "schema:Dataset"

    name: Annotated[str, Scalar("schema:name")]
    homepage: Annotated[str | None, Ref("schema:url")] = None
    published: Annotated[date | None, Typed("schema:datePublished", datatype="xsd:date")] = None
    keywords: Annotated[list[str], StrList("schema:keywords")] = Field(default_factory=list)
    manufacturer: Annotated[str | None, OrgNode(term="schema:manufacturer")] = None
    doi: Annotated[str | None, IdentifierNode()] = None
    technique: Annotated[str | None, DefinedTerm()] = None
    publisher: Annotated[str | Org | None, NodeRef(role="publisher", term="schema:publisher")] = None
    funders: Annotated[list[str | Org], NodeRef(role="funder", term="schema:funder")] = Field(default_factory=list)
    internal_note: str | None = None


class Typing(BdfModel):
    """A model whose ``@type`` comes from a field rather than the class."""

    kind: Annotated[str | None, RdfType(prefix="schema:")] = None
    name: Annotated[str | None, Scalar("schema:name")] = None


def _populated() -> Record:
    """Return a fully-populated fixture record.

    Returns:
        A ``Record`` with every marked field set.
    """
    return Record(
        name="Cycling dataset",
        homepage="https://example.org/dataset",
        published=date(2026, 7, 27),
        keywords=["ageing", "cycling", "lithium"],
        manufacturer="Acme Cells",
        doi="10.5281/zenodo.1",
        technique="Electrochemical Impedance Spectroscopy",
        publisher=Org(name="Battery Data Alliance", ror="052gg0110"),
        funders=[Org(name="Funder One"), Org(name="Funder Two")],
        internal_note="not projected",
    )


# ── CURIE resolution and prefix binding ───────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("schema:name", "https://schema.org/name", id="schema"),
        pytest.param("prov:Activity", "http://www.w3.org/ns/prov#Activity", id="prov"),
        pytest.param(
            "bdf:voltage_volt",
            "https://w3id.org/battery-data-alliance/ontology/battery-data-format#voltage_volt",
            id="bdf",
        ),
        pytest.param("xsd:date", "http://www.w3.org/2001/XMLSchema#date", id="xsd"),
        pytest.param("https://example.org/x", "https://example.org/x", id="absolute-iri"),
    ],
)
def test_uri_resolves_governed_curies_and_iris(value: str, expected: str) -> None:
    """A governed CURIE resolves through its prefix; an absolute IRI passes through.

    Args:
        value: The CURIE or IRI handed to ``_uri``.
        expected: The IRI string it must resolve to.
    """
    assert str(_uri(value)) == expected


@pytest.mark.parametrize(
    "value",
    ["emmo:hasNumberValue", "emmo:Volt", "battinfo:NominalVoltage", "nope:local", "plainstring", ""],
    ids=["emmo-predicate", "emmo-unit", "battinfo-class", "unknown-prefix", "no-colon", "empty"],
)
def test_uri_rejects_emmo_and_unknown_prefixes(value: str) -> None:
    """No EMMO term is reachable by prefix concatenation, and neither is a bare string.

    ``emmo:hasNumericalData`` — a term that does not exist — was emitted for
    months precisely because the prefix table made it writable. The prefixes are
    gone, so the failure mode cannot recur.

    Args:
        value: The unresolvable input.
    """
    with pytest.raises(ValueError, match="unresolvable CURIE"):
        _uri(value)


def test_prefix_table_carries_only_governed_namespaces() -> None:
    """The CURIE table holds only BDF-governed and stable W3C namespaces."""
    assert set(_PREFIXES) == {"schema", "prov", "bdf", "xsd", "rdfs", "rdf"}


def test_bind_prefixes_binds_every_projection_prefix() -> None:
    """Serialisation stays readable: every projection prefix is bound."""
    graph = Graph()
    bind_prefixes(graph)
    bound = {prefix: str(namespace) for prefix, namespace in graph.namespaces()}
    assert all(bound.get(prefix) == str(namespace) for prefix, namespace in _PREFIXES.items())


# ── Per-marker projection shape ───────────────────────────────────────────────


def test_scalar_projects_exactly_one_triple() -> None:
    """A marked scalar field emits one triple under its declared predicate."""
    graph = Record(name="Cycling dataset").to_graph(subject=_SUBJECT)
    assert list(graph.objects(_SUBJECT, SCHEMA.name)) == [Literal("Cycling dataset")]


def test_ref_projects_an_iri_not_a_literal() -> None:
    """A ``Ref`` field's object is a resource, so consumers can follow it."""
    graph = Record(name="d", homepage="https://example.org/dataset").to_graph(subject=_SUBJECT)
    assert graph.value(_SUBJECT, SCHEMA.url) == URIRef("https://example.org/dataset")


def test_typed_projects_a_datatyped_literal() -> None:
    """A ``Typed`` field carries its declared datatype, not a bare string."""
    graph = Record(name="d", published=date(2026, 7, 27)).to_graph(subject=_SUBJECT)
    obj = graph.value(_SUBJECT, SCHEMA.datePublished)
    assert obj.datatype == _uri("xsd:date")
    assert obj.toPython() == date(2026, 7, 27)


def test_strlist_projects_repeated_direct_edges() -> None:
    """A string list is repeated direct edges, never an ordered collection."""
    graph = Record(name="d", keywords=["ageing", "cycling"]).to_graph(subject=_SUBJECT)
    assert set(graph.objects(_SUBJECT, SCHEMA.keywords)) == {Literal("ageing"), Literal("cycling")}


def test_org_node_projects_a_typed_organization_node() -> None:
    """``OrgNode`` mints a typed, named organisation node carrying the name."""
    graph = Record(name="d", manufacturer="Acme Cells").to_graph(subject=_SUBJECT)
    node = graph.value(_SUBJECT, SCHEMA.manufacturer)
    assert node == URIRef(f"{_SUBJECT}/manufacturer")
    assert (node, RDF.type, SCHEMA.Organization) in graph
    assert graph.value(node, SCHEMA.name) == Literal("Acme Cells")


def test_identifier_node_carries_the_doi_property_id() -> None:
    """The DOI node is discriminated by its ``schema:propertyID``."""
    graph = Record(name="d", doi="10.5281/zenodo.1").to_graph(subject=_SUBJECT)
    node = graph.value(_SUBJECT, SCHEMA.identifier)
    assert graph.value(node, SCHEMA.propertyID) == URIRef("https://doi.org/")
    assert graph.value(node, SCHEMA.value) == Literal("10.5281/zenodo.1")


def test_defined_term_types_a_resolvable_technique() -> None:
    """A published technique gains its CHAMEO class alongside ``schema:DefinedTerm``."""
    graph = Record(name="d", technique="Electrochemical Impedance Spectroscopy").to_graph(subject=_SUBJECT)
    node = graph.value(_SUBJECT, SCHEMA.measurementTechnique)
    types = set(graph.objects(node, RDF.type))
    assert SCHEMA.DefinedTerm in types
    assert resolve("ElectrochemicalImpedanceSpectroscopy") in types


def test_defined_term_degrades_for_an_unpublished_technique() -> None:
    """An unresolvable technique stays writable as a plain named term."""
    graph = Record(name="d", technique="Bespoke In-House Wiggle Test").to_graph(subject=_SUBJECT)
    node = graph.value(_SUBJECT, SCHEMA.measurementTechnique)
    assert set(graph.objects(node, RDF.type)) == {SCHEMA.DefinedTerm}
    assert graph.value(node, SCHEMA.name) == Literal("Bespoke In-House Wiggle Test")


def test_same_as_projects_a_resolvable_identifier_iri() -> None:
    """A bare ROR becomes a ``schema:sameAs`` resource under its scheme prefix."""
    graph = Org(name="Battery Data Alliance", ror="052gg0110").to_graph(subject=_SUBJECT)
    assert graph.value(_SUBJECT, SCHEMA.sameAs) == URIRef("https://ror.org/052gg0110")


def test_rdf_type_marker_drives_the_node_type() -> None:
    """A discriminator field sets the node ``@type`` declaratively."""
    graph = Typing(kind="Person", name="Ada").to_graph(subject=_SUBJECT)
    assert (_SUBJECT, RDF.type, SCHEMA.Person) in graph


def test_node_ref_promotes_a_sub_model_to_its_own_node() -> None:
    """A referenced model becomes a named node built from its own markers."""
    graph = _populated().to_graph(subject=_SUBJECT)
    node = graph.value(_SUBJECT, SCHEMA.publisher)
    assert node == URIRef(f"{_SUBJECT}/publisher")
    assert (node, RDF.type, SCHEMA.Organization) in graph
    assert graph.value(node, SCHEMA.name) == Literal("Battery Data Alliance")


def test_node_ref_list_members_get_distinct_nodes() -> None:
    """List members are separate direct edges to separately-named nodes."""
    graph = _populated().to_graph(subject=_SUBJECT)
    nodes = set(graph.objects(_SUBJECT, SCHEMA.funder))
    assert nodes == {URIRef(f"{_SUBJECT}/funder-1"), URIRef(f"{_SUBJECT}/funder-2")}


def test_promoted_node_ids_carry_a_single_fragment_boundary() -> None:
    """A nested identifier extends the parent's fragment; it never opens a second one."""
    graph = _populated().to_graph(subject=_SUBJECT)
    assert all(str(subject).count("#") == 1 for subject in graph.subjects())


def test_node_ref_accepts_a_bare_reference_iri() -> None:
    """A ``str`` target is an IRI reference and produces the same edge."""
    graph = Record(name="d", publisher="agents/bda.jsonld#organization").to_graph(subject=_SUBJECT)
    assert graph.value(_SUBJECT, SCHEMA.publisher) == URIRef("agents/bda.jsonld#organization")


def test_node_ref_reference_iri_survives_the_round_trip() -> None:
    """An unresolved reference decodes back to its IRI rather than an empty model."""
    original = Record(name="d", publisher="agents/bda.jsonld#organization")
    graph = original.to_graph(subject=_SUBJECT)
    assert Record.from_graph(graph, _SUBJECT).publisher == "agents/bda.jsonld#organization"


# ── The walker's rules ────────────────────────────────────────────────────────


def test_unmarked_field_produces_no_triple() -> None:
    """A field with no marker is not projected at all."""
    graph = Record(name="d", internal_note="not projected").to_graph(subject=_SUBJECT)
    assert Literal("not projected") not in set(graph.objects())


def test_absent_fields_produce_no_triples() -> None:
    """An unset marked field emits nothing rather than an empty node."""
    graph = Record(name="d").to_graph(subject=_SUBJECT)
    assert set(graph.predicates(_SUBJECT)) == {RDF.type, SCHEMA.name}


def test_undeclared_keyword_is_rejected() -> None:
    """A model rejects an undeclared keyword rather than storing it as extra data."""
    with pytest.raises(ValidationError):
        Record(name="d", nonsense="value")


def test_marker_metadata_needs_no_serialiser_edit() -> None:
    """Adding a marked field to a model projects it with no walker change.

    The walker is a uniform per-field loop, so a model defined here — after
    ``to_graph`` was written — projects without any model-specific code.
    """

    class Late(BdfModel):
        """A model defined long after the walker was."""

        late_field: Annotated[str | None, Scalar("bdf:late_field")] = None

    graph = Late(late_field="present").to_graph(subject=_SUBJECT)
    assert graph.value(_SUBJECT, BDF.late_field) == Literal("present")


# ── Structural invariants ─────────────────────────────────────────────────────


def test_no_emitted_graph_contains_an_rdf_list_node() -> None:
    """Repeated values are direct edges; no collection node is ever emitted."""
    graph = _populated().to_graph(subject=_SUBJECT)
    assert not set(graph.subject_objects(RDF.first))
    assert not set(graph.subject_objects(RDF.rest))
    assert RDF.nil not in set(graph.objects())


def test_written_document_has_no_blank_subject(tmp_path) -> None:
    """A written document derives its subject from its own name, never a blank node.

    Asserted against the document text rather than a parsed graph: parsing would
    resolve the relative identifiers against the reader's working directory, and
    what matters is what the file itself says.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = _populated().write(tmp_path / "dataset.jsonld")
    doc = json.loads(path.read_text(encoding="utf-8"))
    ids = [node["@id"] for node in doc["@graph"]]
    assert ids
    assert all(node_id.startswith("dataset.jsonld#record") for node_id in ids)


def test_compact_jsonld_without_a_base_is_refused() -> None:
    """Rather than fall back to a blank root, serialisation refuses."""
    with pytest.raises(ValueError, match="never blank"):
        to_compact_jsonld(_populated())


def test_document_subject_is_document_relative() -> None:
    """Identifiers embed the document's own name, not an absolute location."""
    assert str(document_subject("/tmp/store/dataset.jsonld")) == "dataset.jsonld#record"


# ── Canonical equivalence and round-trips ─────────────────────────────────────


def test_reordered_serialisation_compares_equal() -> None:
    """Triple order and formatting do not affect canonical equivalence."""
    graph = _populated().to_graph(subject=_SUBJECT)
    shuffled = Graph()
    for triple in sorted(graph, key=str, reverse=True):
        shuffled.add(triple)
    assert graphs_equivalent(graph, shuffled)
    assert canonical_nquads(graph) == canonical_nquads(shuffled)


def test_changed_literal_compares_unequal() -> None:
    """A single altered literal is detected, so equivalence is not vacuous."""
    graph = _populated().to_graph(subject=_SUBJECT)
    altered = Graph()
    altered += graph
    altered.remove((_SUBJECT, SCHEMA.name, Literal("Cycling dataset")))
    altered.add((_SUBJECT, SCHEMA.name, Literal("Different name")))
    assert not graphs_equivalent(graph, altered)


def test_graph_round_trip_reproduces_the_model() -> None:
    """``from_graph(to_graph(model))`` reproduces every marked field."""
    original = _populated()
    decoded = Record.from_graph(original.to_graph(subject=_SUBJECT), _SUBJECT)
    assert decoded.name == original.name
    assert decoded.homepage == original.homepage
    assert decoded.published == original.published
    assert decoded.keywords == original.keywords
    assert decoded.manufacturer == original.manufacturer
    assert decoded.doi == original.doi
    assert decoded.technique == original.technique
    assert decoded.publisher == original.publisher
    assert decoded.funders == original.funders


def test_graph_round_trip_is_stable_under_reprojection() -> None:
    """Re-projecting a decoded model reproduces an equivalent graph."""
    graph = _populated().to_graph(subject=_SUBJECT)
    reprojected = Record.from_graph(graph, _SUBJECT).to_graph(subject=_SUBJECT)
    assert graphs_equivalent(graph, reprojected)


def test_plain_json_round_trip_preserves_the_model() -> None:
    """Plain JSON is Pydantic's own serialisation and carries the same facts."""
    original = _populated()
    assert Record.model_validate(original.model_dump()) == original


def test_compact_jsonld_round_trip_needs_no_network(tmp_path) -> None:
    """A document written and read back reproduces the model from vendored assets.

    Args:
        tmp_path: Pytest temporary directory.
    """
    original = _populated()
    text = to_compact_jsonld(original, path=tmp_path / "dataset.jsonld")
    decoded = from_compact_jsonld(Record, text)
    assert decoded.name == original.name
    assert decoded.publisher == original.publisher


def test_value_node_decode_ignores_a_non_matching_candidate() -> None:
    """A sibling node under the same edge is skipped when its identity differs."""
    record = Record(name="d", doi="10.5281/zenodo.1")
    graph = record.to_graph(subject=_SUBJECT)
    decoy = URIRef(f"{_SUBJECT}/decoy")
    graph.add((decoy, RDF.type, SCHEMA.PropertyValue))
    graph.add((decoy, SCHEMA.propertyID, URIRef("https://handle.net/")))
    graph.add((decoy, SCHEMA.value, Literal("wrong")))
    graph.add((_SUBJECT, SCHEMA.identifier, decoy))
    assert Record.from_graph(graph, _SUBJECT).doi == "10.5281/zenodo.1"


def test_ctx_reports_list_fields() -> None:
    """The walker's context knows list-ness, which the graph no longer encodes."""
    assert Ctx(field_name="x").is_list is False
