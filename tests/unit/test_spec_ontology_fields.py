"""Ontology-sourced metadata on Quantity: obligation, docs fields, derivations, units.

The bundled snapshot (ontology >= 1.1.0) is the source of truth for these
fields; the tests below pin both the extraction logic and the behavioural
contract that obligation drives requiredness.
"""

from __future__ import annotations

import importlib.resources

from rdflib import Graph, URIRef

from bdf import spec, vocabulary
from bdf.spec import COLUMN_ONTOLOGY, ColumnOntology

# Behavioural contract: changing :obligation in an ontology release changes
# validate_df() behaviour. This set must only be updated deliberately, in
# the same PR that adopts the new ontology snapshot.
EXPECTED_REQUIRED = {"test_time_second", "voltage_volt", "current_ampere"}
EXPECTED_RECOMMENDED = {
    "unix_time_second",
    "step_count",
    "cycle_count",
    "ambient_temperature_celsius",
}


def test_required_set_matches_ontology_obligations() -> None:
    actual = {name for name, q in COLUMN_ONTOLOGY if q.required and not q.deprecated}
    assert actual == EXPECTED_REQUIRED


def test_recommended_set_matches_ontology_obligations() -> None:
    actual = {name for name, q in COLUMN_ONTOLOGY if q.obligation == "recommended" and not q.deprecated}
    assert actual == EXPECTED_RECOMMENDED


def test_every_active_quantity_has_an_obligation() -> None:
    missing = [name for name, q in COLUMN_ONTOLOGY if not q.deprecated and not q.obligation]
    assert missing == []


def test_obligation_values_are_known() -> None:
    levels = {q.obligation for _, q in COLUMN_ONTOLOGY if q.obligation}
    assert levels <= {"required", "recommended", "optional"}


def test_deprecated_terms_carry_no_obligation_and_are_never_required() -> None:
    deprecated = [(name, q) for name, q in COLUMN_ONTOLOGY if q.deprecated]
    assert deprecated, "snapshot should contain deprecated tombstones"
    for name, q in deprecated:
        assert q.obligation == "", name
        assert not q.required, name


def test_description_and_definition_extracted() -> None:
    # Assert structure, not exact prose: wording evolves with ontology
    # releases, and pinning it here would couple every snapshot sync to a
    # test edit. Behavioural contracts (obligations, required set) are the
    # ones pinned exactly.
    q = COLUMN_ONTOLOGY["current_ampere"]
    assert q.description
    assert "current" in q.description.lower()
    assert q.definition
    # description is the short, table-friendly text
    assert len(q.description) <= len(q.definition)


def test_latex_symbol_and_formula_extracted() -> None:
    assert COLUMN_ONTOLOGY["current_ampere"].latex_symbol == "I"
    cum = COLUMN_ONTOLOGY["step_cumulative_capacity_ah"]
    assert cum.latex_symbol
    assert "\\int" in cum.latex_formula


def test_derived_from_resolves_to_mr_names() -> None:
    cum = COLUMN_ONTOLOGY["step_cumulative_capacity_ah"]
    assert cum.derived_from == ("current_ampere", "step_time_second")
    net = COLUMN_ONTOLOGY["net_capacity_ah"]
    assert net.derived_from == ("charging_capacity_ah", "discharging_capacity_ah")
    for name in cum.derived_from + net.derived_from:
        assert name in COLUMN_ONTOLOGY


_MINIMAL_PRE_OBLIGATION_TTL = """
@prefix : <https://w3id.org/battery-data-alliance/ontology/battery-data-format#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

:test_time_second a owl:Class ;
    skos:prefLabel "Test Time / s"@en .

:power_watt a owl:Class ;
    skos:prefLabel "Power / W"@en .
"""


def test_required_falls_back_to_default_without_obligations() -> None:
    g = Graph()
    g.parse(data=_MINIMAL_PRE_OBLIGATION_TTL, format="turtle")
    onto = ColumnOntology.from_graph(g)
    # Without :obligation annotations the level is synthesized from the
    # static fallback set; `required` is derived from it.
    assert onto["test_time_second"].obligation == "required"
    assert onto["test_time_second"].required
    assert onto["power_watt"].obligation == "optional"
    assert not onto["power_watt"].required


# --------- Published unit annotations ----------

# The bundled snapshot annotates every unit-bearing column with
# `schema:unitCode` and `schema:unitText`. `Quantity` exposes both verbatim,
# alongside the normalized `unit` that conversion and column handling already
# use. The tests below pin that the addition is purely additive, and that the
# EMMO measurement-unit restriction — which names classes absent from the
# pinned BattINFO context — is never consulted.

_SCHEMA_UNIT_CODE = URIRef("https://schema.org/unitCode")
_SCHEMA_UNIT_TEXT = URIRef("https://schema.org/unitText")
_OWL_SOME_VALUES_FROM = URIRef("http://www.w3.org/2002/07/owl#someValuesFrom")


def _snapshot_graph() -> Graph:
    data = importlib.resources.files("bdf.data").joinpath("bdf-ontology-snapshot.ttl").read_bytes()
    return spec._graph_from_bytes(data, format="turtle")


def test_unit_is_unchanged_by_the_annotation_fields() -> None:
    # `unit` stays exactly what the pre-existing rule produced: the first
    # schema:unitCode literal put through _normalize_unit, or None when the
    # term carries no unit code. Derived from the graph independently of
    # Quantity so the assertion cannot drift with the parser.
    g = _snapshot_graph()
    checked = 0
    for _, q in COLUMN_ONTOLOGY:
        codes = [str(lit) for lit in g.objects(URIRef(q.iri), _SCHEMA_UNIT_CODE) if str(lit)]
        expected = spec._normalize_unit(codes[0]) if codes else None
        assert q.unit == expected, q.mr_name
        checked += 1
    assert checked, "snapshot should contain quantities"


def test_capacity_term_exposes_verbatim_unit_annotations() -> None:
    q = COLUMN_ONTOLOGY["net_capacity_ah"]
    assert q.unit_code == "A.h"
    assert q.unit_text == "ampere hour"
    # The normalized form remains distinct from the published code.
    assert q.unit == "Ah"


def test_unit_annotations_match_the_snapshot_verbatim() -> None:
    g = _snapshot_graph()
    for _, q in COLUMN_ONTOLOGY:
        subject = URIRef(q.iri)
        codes = [str(lit) for lit in g.objects(subject, _SCHEMA_UNIT_CODE) if str(lit)]
        texts = [str(lit) for lit in g.objects(subject, _SCHEMA_UNIT_TEXT) if str(lit)]
        assert q.unit_code == (codes[0] if codes else ""), q.mr_name
        assert q.unit_text == (texts[0] if texts else ""), q.mr_name


def test_terms_with_a_dangling_emmo_unit_restriction_still_resolve() -> None:
    # Thirty snapshot terms restrict their measurement unit to emmo:AmpereHour
    # or emmo:WattHour, neither of which exists at the pinned BattINFO version.
    g = _snapshot_graph()
    restricted = {
        str(obj).rsplit("#", 1)[-1]
        for obj in g.objects(None, _OWL_SOME_VALUES_FROM)
        if isinstance(obj, URIRef) and str(obj).startswith("https://w3id.org/emmo#")
    }
    # Opaque EMMO IRIs carry no readable key, so has_term cannot speak to them;
    # only the label-shaped names are claims the pinned context can answer.
    named = {name for name in restricted if not name.startswith("EMMO_")}
    unresolvable = {name for name in named if not vocabulary.has_term(name)}
    assert unresolvable, "snapshot should reference at least one absent EMMO unit class"
    assert "AmpereHour" in unresolvable

    affected = [q for _, q in COLUMN_ONTOLOGY if q.unit_code in ("A.h", "W.h")]
    assert affected, "snapshot should carry capacity or energy columns"
    for q in affected:
        # Resolution succeeds and the unit comes from the published
        # annotations, not from the unresolvable restriction.
        assert q.unit_code and q.unit_text
        assert q.unit in ("Ah", "Wh")
        # The restriction is not followed: no field of the resolved Quantity
        # carries the EMMO class it names, in any form.
        rendered = q.model_dump_json()
        for name in unresolvable:
            assert name not in rendered, (q.mr_name, name)
