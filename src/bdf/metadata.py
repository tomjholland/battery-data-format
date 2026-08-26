# src/bdf/metadata.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from pydantic import (
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from bdf.battinfo._base import _RecordModel
from bdf.battinfo.record import Record
from bdf.battinfo.schema import BATTINFO_SCHEMA

if TYPE_CHECKING:
    from bdf.battinfo.records import (
        BattinfoCellInstance,
        BattinfoChannelInstance,
        BattinfoDatasetSchemaOrgAligned,
        BattinfoEquipmentInstance,
        BattinfoTest,
        BattinfoTestProtocol,
    )

# Optional pandas (only required if you pass a DataFrame)
try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

from bdf import spec

# The bundle-relative document each entity's root record is bounded by,
# matching the wrapper key a `Metadata` field answers to.
_ENTITY_DOCS: dict[str, str] = {
    "battinfo_test": "test.schema.json",
    "battinfo_cell": "cell-instance.schema.json",
    "battinfo_channel": "channel.schema.json",
    "battinfo_equipment": "equipment.schema.json",
    "battinfo_test_protocol": "test-protocol.schema.json",
    "battinfo_dataset": "dataset.schema.json",
}


# The root-node cache, and the `docs` mapping it was filled from. Holding
# that mapping here keeps it alive, so its address cannot be reused for a
# later substituted set while the cache still holds entries built from it;
# an `id`-only comparison has no such guarantee once the mapping it named is
# freed.
_root_cache: dict[str, tuple[dict[str, Any], str]] = {}
_root_cache_docs: dict[str, dict[str, Any]] | None = None


def _resolve_root(doc_name: str) -> tuple[dict[str, Any], str]:
    """Resolve one BattINFO entity's root schema node.

    The root documents never change at runtime, so the result is
    cached. A caller that substitutes `BATTINFO_SCHEMA` replaces its `docs`
    mapping outright, so a fresh `docs` mapping (`is` compared against the
    one the cache was filled from) clears the cache before it resolves.

    Args:
        doc_name: The bundle-relative document naming the entity's root node.

    Returns:
        The resolved root node, and the `$id` it resolved against.
    """
    global _root_cache_docs
    if BATTINFO_SCHEMA.docs is not _root_cache_docs:
        _root_cache.clear()
        _root_cache_docs = BATTINFO_SCHEMA.docs
    cached = _root_cache.get(doc_name)
    if cached is None:
        doc = BATTINFO_SCHEMA.docs[doc_name]
        cached = BATTINFO_SCHEMA.resolve(doc, doc["$id"])
        _root_cache[doc_name] = cached
    return cached


def _root_record(doc_name: str, data: dict[str, Any] | None = None) -> Record:
    """Build a `Record` bounded by one BattINFO entity's root schema node.

    Args:
        doc_name: The bundle-relative document naming the entity's root node.
        data: The backing dictionary the record reads and writes, or an
            empty one where nothing was set.

    Returns:
        A record bounded by the document's root node, over `data`.
    """
    node, base_id = _resolve_root(doc_name)
    return Record(BATTINFO_SCHEMA, node, base_id, data if data is not None else {}, path="")


class BdfReadInfo(_RecordModel):
    """BDF-owned read audit facts, kept out of the BattINFO records."""

    source: str | None = None
    time_reconciliation: list[dict] | None = None
    bdf_version: str | None = None
    """The batterydf package version that wrote this sidecar. Stamped by
    ``save()`` on every sidecar it writes, overwriting any earlier stamp: the
    stamps identify the file's writer, so a read-then-save records the version
    that performed the save, not the one that wrote the file it read."""
    ontology_version: str | None = None
    """The BDF ontology release the writer's column spec was pinned to."""
    battinfo_ref: str | None = None
    """The upstream BattINFO commit the writer's bundled schemas were fetched
    at (a release tag once upstream versions its schemas)."""


class Metadata(_RecordModel):
    """The metadata a BDF read returns: the BattINFO entity records, BDF's own
    read audit section, the verbatim source a parser read, and the values a
    rule staged deliberately.

    Each record is bounded by the loaded BattINFO schema
    (:mod:`bdf.battinfo.schema`), and the nesting and property names are
    BattINFO's own (``meta.battinfo_test.test.instrument_name``, not a
    flattened facade), so a caller who already speaks BattINFO finds nothing
    to translate. A plugin fills only the fields it can derive, and every
    other declared leaf field keeps its default. The saved sidecar serialises
    this same shape.

    Every record field auto-constructs, so a caller assigns a leaf on a fresh
    instance with no ``None`` guard. A leaf reads as ``None`` while unfilled,
    a choice-typed leaf (``Quantity.value``, ``expires_at``,
    ``manufactured_at``, ``commissioned_at`` among them) holds its value
    directly, and a field whose node declares its own fields returns a nested
    record.
    """

    # An upstream schema sets `additionalProperties: false` at its root, so
    # `bdf`, `raw`, and `extras` cannot live inside a record. Each wrapper key
    # joins `battinfo_` to the upstream entity type, which also keeps a
    # sidecar document distinct from a record document.
    if TYPE_CHECKING:
        # Annotated against the generated stub classes here alone, so a type
        # checker sees the fields `bdf.battinfo.records` declares. The stub
        # module reaches no run time: the field stays a plain `Record` there,
        # exactly as the branch below declares it.
        battinfo_test: BattinfoTest
        battinfo_cell: BattinfoCellInstance
        battinfo_channel: BattinfoChannelInstance
        battinfo_equipment: BattinfoEquipmentInstance
        battinfo_test_protocol: BattinfoTestProtocol
        battinfo_dataset: BattinfoDatasetSchemaOrgAligned
    else:
        battinfo_test: Record = Field(default_factory=lambda: _root_record("test.schema.json"))
        """The test activity that produced this data: who ran it, when it started, and which datasets it links."""
        battinfo_cell: Record = Field(default_factory=lambda: _root_record("cell-instance.schema.json"))
        """The physical cell the test ran on, and the cell type it derives from."""
        battinfo_channel: Record = Field(default_factory=lambda: _root_record("channel.schema.json"))
        """The one channel of the equipment unit that ran the test."""
        battinfo_equipment: Record = Field(default_factory=lambda: _root_record("equipment.schema.json"))
        """The physical equipment unit that ran the test, a named cycler for example."""
        battinfo_test_protocol: Record = Field(default_factory=lambda: _root_record("test-protocol.schema.json"))
        """The reusable protocol the test followed: its typed steps, and links to the files that state them."""
        battinfo_dataset: Record = Field(default_factory=lambda: _root_record("dataset.schema.json"))
        """The dataset this file belongs to: its distributions (raw and processed files,
        checksums, formats), identifiers, and links to the cell and test that produced it.
        Mirrors a pre-1.0 upstream schema and may evolve with it. Nothing fills this
        record automatically yet: it is the documented place for a user or a downstream
        tool (e.g. the battinfo package) to record file-level provenance (#92)."""
    bdf: BdfReadInfo = Field(default_factory=BdfReadInfo)
    """BDF's own audit of this read: the source it read, and every rescale that the time reconciliation applied."""

    raw: dict[str, Any] | str | None = None
    """The source a parser read, verbatim: the decoded JSON document for a
    JSON-backed parser, the preamble text a text parser matched against, or
    ``None`` when a read captured no source. Holds every key a rule did not
    map, nested keys and array elements included, with no traversal required
    to keep it lossless. A parser reading BDF's own sidecar restores this
    field rather than recapturing the document, so a repeated save and read
    never nests a copy of the sidecar inside itself. Written by `save()` and
    restored on a later read."""
    extras: dict[str, Any] | None = None
    """Keys a rule staged under an explicit extras target, verbatim (JSON
    ``null`` values included). Nothing reaches this field automatically: a
    key no rule maps is preserved by ``raw`` instead. It never holds a failed
    value, because such a value raises. ``None`` when a read staged nothing
    here. Written by `save()` and restored on a later read."""

    @field_validator(
        "battinfo_test",
        "battinfo_cell",
        "battinfo_channel",
        "battinfo_equipment",
        "battinfo_test_protocol",
        "battinfo_dataset",
        mode="before",
    )
    @classmethod
    def _coerce_entity_record(cls, value: Any, info: ValidationInfo) -> Any:
        """Build the field's bounded record from a plain mapping.

        Args:
            value: The value passed for the field: a `Record` already, or a
                mapping in the entity's own BattINFO shape.
            info: Validation context, read for the field's own name.

        Returns:
            `value` unchanged where it is already a `Record`, otherwise a
            record bounded by the field's entity, over a copy of `value`.
        """
        if isinstance(value, Record):
            return value
        doc_name = _ENTITY_DOCS[info.field_name]  # type: ignore[index]
        return _root_record(doc_name, dict(value) if value else {})

    @model_validator(mode="after")
    def _validate_entity_records(self) -> "Metadata":
        """Refuse a value any entity record's own schema node refuses.

        A field built from a mapping carries whatever the mapping stated,
        with no per-value check of its own, so a construction-time refusal
        is enforced here instead, across every entity record at once. A
        stated null is unset, not a refused value, so validation reads the
        pruned form of each record, where a stated null is already absent.

        Returns:
            This instance, unchanged.

        Raises:
            ValueError: An entity record carries a value its own node
                refuses. Every refusal reaches the message, joined.
        """
        errors: list[str] = []
        for name, doc_name in _ENTITY_DOCS.items():
            pruned = getattr(self, name).to_dict()
            if pruned:
                errors.extend(_root_record(doc_name, pruned).validate())
        if errors:
            raise ValueError("; ".join(errors))
        return self


def _unit_text_from_label(label: str) -> Optional[str]:
    """Return the unit portion of a canonical 'Name / UNIT' BDF label, or None."""
    return spec.unit_from_label(label)


BDF_CSVW_SCHEMA_URL = "https://w3id.org/battery-data-alliance/ontology/battery-data-format/schema"
EMMO_BATTERY_CONTEXT = "https://w3id.org/emmo/domain/battery/context"
SCHEMA_CONTEXT = {
    "schema": "https://schema.org/",
    "csvw": "http://www.w3.org/ns/csvw#",
    "bdf": "https://w3id.org/bdf/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
}
DEFAULT_JSONLD_CONTEXT = (EMMO_BATTERY_CONTEXT, SCHEMA_CONTEXT)

MATERIAL_TERM_MAP = {
    "lfp": "LithiumIronPhosphate",
    "lco": "LithiumCobaltOxide",
    "nmc": "LithiumNickelManganeseCobaltOxide",
    "nmc111": "LithiumNickelManganeseCobaltOxide111",
    "nmc811": "LithiumNickelManganeseCobaltOxide811",
    "nmc532": "LithiumNickelManganeseCobaltOxide532",
    "nca": "LithiumNickelCobaltAluminiumOxide",
    "graphite": "Graphite",
    "gr": "Graphite",
    "c6": "Graphite",
    "lto": "LithiumTitanate",
    "lithium": "Lithium",
    "li": "Lithium",
    "silicon": "Silicon",
    "si": "Silicon",
    "cobalt": "Cobalt",
    "co": "Cobalt",
    "lno": "LithiumNickelOxide",
    "lmo": "LithiumManganeseOxide",
    "lnmo": "LithiumNickelManganeseOxide",
    "lmfp": "LithiumManganeseIronPhosphate",
    "nickel": "Nickel",
    "ni": "Nickel",
    "manganese": "Manganese",
    "mn": "Manganese",
    "iron": "Iron",
    "fe": "Iron",
    "vanadium": "Vanadium",
    "v": "Vanadium",
}


# ---------------------------
# Dataclasses
# ---------------------------


@dataclass
class Creator:
    """schema.org Person or Organization; emits ORCID/ROR in sameAs."""

    name: str
    type: str = "Person"  # 'Person' or 'Organization'
    orcid: Optional[str] = None  # Person: ORCID (URL or bare)
    ror: Optional[str] = None  # Org: ROR (URL or bare)
    given_name: Optional[str] = None  # Person
    family_name: Optional[str] = None  # Person
    affiliation: Optional[str] = None  # Person -> Organization name

    def _id_or_sameas(self) -> Dict[str, Any]:
        same_as = None
        if self.type.lower() == "person" and self.orcid:
            same_as = self.orcid if self.orcid.startswith("http") else f"https://orcid.org/{self.orcid}"
        if self.type.lower() == "organization" and self.ror:
            same_as = self.ror if self.ror.startswith("http") else f"https://ror.org/{self.ror}"
        return {"schema:sameAs": same_as} if same_as else {}

    def to_schema_org(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "@type": "schema:Person" if self.type.lower() == "person" else "schema:Organization",
            "schema:name": self.name,
            **self._id_or_sameas(),
        }
        if self.type.lower() == "person":
            if self.given_name:
                node["schema:givenName"] = self.given_name
            if self.family_name:
                node["schema:familyName"] = self.family_name
            if self.affiliation:
                node["schema:affiliation"] = {
                    "@type": "schema:Organization",
                    "schema:name": self.affiliation,
                }
        return node


@dataclass
class PropertyValue:
    """For variableMeasured as PropertyValue."""

    name: str
    property_id: Optional[str] = None  # e.g., your BDF IRI
    unit_text: Optional[str] = None  # e.g., "V", "A", "A*h", "degC"

    def to_schema_org(self) -> Dict[str, Any]:
        out = {"@type": "schema:PropertyValue", "schema:name": self.name}
        if self.property_id:
            out["schema:propertyID"] = self.property_id
        if self.unit_text:
            out["schema:unitText"] = self.unit_text
        return out


@dataclass
class RelatedIdentifier:
    identifier: str
    relation: str = "isSupplementTo"
    scheme: Optional[str] = None

    def to_zenodo(self) -> Dict[str, Any]:
        d = {"identifier": self.identifier, "relation": self.relation}
        if self.scheme:
            d["scheme"] = self.scheme
        return d


@dataclass
class Battery:
    """
    Lightweight battery profile (human-facing).
    """

    id: str
    iri: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    chemistry: Optional[str] = None
    iec_code: Optional[str] = None
    form_factor: Optional[str] = None
    nominal_voltage_v: Optional[float] = None
    rated_capacity_ah: Optional[float] = None
    rated_energy_wh: Optional[float] = None
    mass_g: Optional[float] = None
    volume_l: Optional[float] = None
    pe_materials: List[str] = field(default_factory=list)
    ne_materials: List[str] = field(default_factory=list)

    def to_dict(self, *, drop_none: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "iri": self.iri,
            "model": self.model,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "chemistry": self.chemistry,
            "iec_code": self.iec_code,
            "form_factor": self.form_factor,
            "nominal_voltage_v": self.nominal_voltage_v,
            "rated_capacity_ah": self.rated_capacity_ah,
            "rated_energy_wh": self.rated_energy_wh,
            "mass_g": self.mass_g,
            "volume_l": self.volume_l,
            "pe_materials": list(self.pe_materials),
            "ne_materials": list(self.ne_materials),
        }
        if drop_none:
            out = {k: v for k, v in out.items() if v is not None}
        return out

    def to_schemaorg(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {"@type": ["schema:Product", "Battery"]}

        if self.iri:
            node["@id"] = self.iri
        elif self.id:
            node["@id"] = f"bdf:battery/{self.id}"

        schema_identifier: Optional[Union[str, List[str]]] = None
        if self.id:
            schema_identifier = [self.id, self.iec_code] if self.iec_code and self.iec_code != self.id else self.id
        elif self.iec_code:
            schema_identifier = self.iec_code
        if schema_identifier:
            node["schema:identifier"] = schema_identifier

        display_name = self.name or self.model
        if display_name:
            node["schema:name"] = display_name
        if self.model:
            node["schema:model"] = self.model
        if self.manufacturer:
            node["schema:manufacturer"] = {
                "@type": "schema:Organization",
                "schema:name": self.manufacturer,
            }
        if self.chemistry:
            node["schema:category"] = self.chemistry

        schema_props: List[Dict[str, Any]] = []

        def _add_schema_prop(name: str, property_id: str, value: Any, unit_text: Optional[str] = None) -> None:
            if value is None:
                return
            prop: Dict[str, Any] = {
                "@type": "schema:PropertyValue",
                "schema:name": name,
                "schema:propertyID": property_id,
                "schema:value": value,
            }
            if unit_text:
                prop["schema:unitText"] = unit_text
            schema_props.append(prop)

        _add_schema_prop("IEC code", "bdf:iec_code", self.iec_code)
        _add_schema_prop("Chemistry", "bdf:chemistry", self.chemistry)
        _add_schema_prop("Form factor", "bdf:form_factor", self.form_factor)
        _add_schema_prop("Nominal voltage", "bdf:nominal_voltage_v", self.nominal_voltage_v, "V")
        _add_schema_prop("Rated capacity", "bdf:rated_capacity_ah", self.rated_capacity_ah, "Ah")
        _add_schema_prop("Rated energy", "bdf:rated_energy_wh", self.rated_energy_wh, "Wh")
        _add_schema_prop("Mass", "bdf:mass_g", self.mass_g, "g")
        _add_schema_prop("Volume", "bdf:volume_l", self.volume_l, "L")

        if schema_props:
            node["schema:additionalProperty"] = schema_props

        emmo_props: List[Dict[str, Any]] = []

        def _add_emmo_prop(prop_type: str, label: str, value: Any, unit: str) -> None:
            if value is None:
                return
            emmo_props.append(
                {
                    "@type": prop_type,
                    "rdfs:label": label,
                    "hasNumberValue": value,
                    "hasMeasurementUnit": unit,
                }
            )

        _add_emmo_prop("NominalVoltage", "nominal voltage", self.nominal_voltage_v, "emmo:Volt")
        _add_emmo_prop("RatedCapacity", "rated capacity", self.rated_capacity_ah, "emmo:AmpereHour")
        _add_emmo_prop("RatedEnergy", "rated energy", self.rated_energy_wh, "emmo:WattHour")
        _add_emmo_prop("Mass", "mass", self.mass_g, "emmo:Gram")
        _add_emmo_prop("Volume", "volume", self.volume_l, "emmo:Litre")

        if emmo_props:
            node["hasProperty"] = emmo_props

        def _material_nodes(materials: List[str]) -> List[Dict[str, Any]]:
            nodes: List[Dict[str, Any]] = []
            for material in materials:
                term = _material_term(material)
                if term:
                    nodes.append({"@type": term, "rdfs:label": term})
                else:
                    nodes.append({"@type": "Material", "rdfs:label": material})
            return nodes

        if self.pe_materials:
            node["hasPositiveElectrode"] = {
                "@type": "Electrode",
                "rdfs:label": "positive electrode",
                "hasActiveMaterial": _material_nodes(self.pe_materials),
            }
        if self.ne_materials:
            node["hasNegativeElectrode"] = {
                "@type": "Electrode",
                "rdfs:label": "negative electrode",
                "hasActiveMaterial": _material_nodes(self.ne_materials),
            }

        return node


@dataclass
class DataDownload:
    """
    schema.org DataDownload (downloadable distribution)
    + optional CSVW metadata (embedded under Dataset.mainEntity).
    """

    url: str  # contentUrl
    name: Optional[str] = None
    encoding_format: Optional[str] = None  # e.g., "text/csv", "application/zip"
    description: Optional[str] = None
    id: Optional[str] = None  # defaults to url

    # CSVW extras (optional, used to embed a CSVW Table node in mainEntity)
    csvw_table_schema_url: Optional[str] = None
    csvw_table_id: Optional[str] = None

    def _csvw_id(self) -> Optional[str]:
        if not self.csvw_table_schema_url:
            return None
        return self.csvw_table_id or f"{(self.id or self.url)}#csvw"

    def to_schema_org(self) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "@type": "schema:DataDownload",
            "@id": self.id or self.url,
            "schema:name": self.name or self.url.split("/")[-1],
            "schema:contentUrl": self.url,
        }
        if self.encoding_format:
            node["schema:encodingFormat"] = self.encoding_format
        if self.description:
            node["schema:description"] = self.description
        return node

    def to_csvw_table_embedded(self) -> Optional[Dict[str, Any]]:
        """Return a CSVW Table object for inline embedding under Dataset.mainEntity."""
        if not self.csvw_table_schema_url:
            return None
        return {
            "@id": self._csvw_id(),
            "@type": "csvw:Table",
            "csvw:url": self.url,
            "csvw:tableSchema": self.csvw_table_schema_url,
        }

    @classmethod
    def for_file(cls, path: Union[str, Path], *, csvw_schema_url: Optional[str] = None) -> DataDownload:
        """
        Convenience to build a DataDownload from a local file path.
        """
        p = Path(path)
        return cls(url=p.name, name=p.name, encoding_format=None, id=None, csvw_table_schema_url=csvw_schema_url)


# ---------------------------
# Helpers
# ---------------------------


def _license_to_url(license_value: str) -> str:
    """Accept a full URL or an SPDX id (e.g., 'CC-BY-4.0') and return a URL."""
    if license_value.startswith(("http://", "https://")):
        return license_value
    return f"https://spdx.org/licenses/{license_value}"


def _normalize_material_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _material_term(value: str) -> Optional[str]:
    if not value:
        return None
    return MATERIAL_TERM_MAP.get(_normalize_material_key(value))


def _left_of_label(label: str) -> str:
    return label.split("/", 1)[0].strip()


def _spec_match_by_left(left: str) -> Optional[Dict[str, Any]]:
    """Return the spec column entry dict for a given preferred-label 'left' text."""
    for _mr, qty in spec.COLUMN_ONTOLOGY:
        if _left_of_label(qty.label_template).lower() == left.lower():
            return {"iri": qty.iri}
    return None


def _required_pvs_from_spec() -> List[Dict[str, Any]]:
    """
    Build default PropertyValue list for required quantities directly from spec.COLUMNS.
    Uses label_template -> left name, unit, and iri.
    If spec is unavailable, returns [] (caller has emergency fallback).
    """
    pvs: List[Dict[str, Any]] = []
    for _mr, qty in spec.COLUMN_ONTOLOGY:
        if qty.required:
            name = _left_of_label(qty.label_template) if qty.label_template else qty.mr_name
            pvs.append(PropertyValue(name=name, property_id=qty.iri, unit_text=qty.unit).to_schema_org())
    return pvs


def _variable_measured_from_df(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Build a list of schema.org PropertyValue from DataFrame columns:
      - Prefer df.attrs["bdf:columns"] for quantity+unit if present
      - Else, derive name from column label left side; unit via spec.unit_from_label
      - Attach BDA IRI if found in spec (by matching preferred-label left side)
      - Keep numeric-looking columns by default
    """
    if pd is None or not isinstance(df, pd.DataFrame):
        return []

    items: List[Dict[str, Any]] = []

    # 1) Use normalization metadata if available (most accurate)
    meta = getattr(df, "attrs", {}).get("bdf:columns", {})
    if isinstance(meta, dict) and meta:
        for canon_label, info in meta.items():
            name = _left_of_label(str(canon_label))
            unit_text = _unit_text_from_label(canon_label)
            if not unit_text:
                unit_text = str(info.get("unit")) if info.get("unit") else None

            iri = None
            match = _spec_match_by_left(name)
            if match:
                iri = match.get("iri")

            items.append(PropertyValue(name=name, property_id=iri, unit_text=unit_text).to_schema_org())

        # Deduplicate by (name, propertyID)
        seen = set()
        dedup: List[Dict[str, Any]] = []
        for it in items:
            key = (it.get("schema:name"), it.get("schema:propertyID"))
            if key not in seen:
                seen.add(key)
                dedup.append(it)
        return dedup

    # 2) Fallback: infer from headers directly for numeric columns
    for col in df.columns:
        s = df[col]
        if not pd.api.types.is_numeric_dtype(s):
            continue
        left = _left_of_label(str(col))
        unit_text = _unit_text_from_label(str(col))
        iri = None
        m = _spec_match_by_left(left)
        if m:
            iri = m.get("iri")
        items.append(PropertyValue(name=left, property_id=iri, unit_text=unit_text).to_schema_org())

    # Deduplicate
    seen = set()
    dedup = []
    for it in items:
        key = (it.get("schema:name"), it.get("schema:propertyID"))
        if key not in seen:
            seen.add(key)
            dedup.append(it)
    return dedup


# ---------------------------
# Dataset metadata
# ---------------------------


@dataclass
class Dataset:
    # Core
    title: str
    creators: List[Creator]
    description: str

    # Common
    keywords: List[str] = field(default_factory=list)
    license: str = "CC-BY-4.0"
    version: Optional[str] = None
    publication_date: Optional[str] = None
    language: Optional[str] = "en"

    # Google / schema.org friendly additions
    url: Optional[str] = None  # landing page URL
    same_as: List[str] = field(default_factory=list)
    identifiers: List[str] = field(default_factory=list)  # include DOI/compact IDs
    citation: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    is_based_on: List[str] = field(default_factory=list)
    variable_measured: List[Union[str, PropertyValue]] = field(default_factory=list)
    measurement_technique: Optional[Union[str, List[str]]] = None
    spatial_coverage: Optional[str] = None
    temporal_coverage: Optional[str] = None
    included_in_data_catalog: Optional[Dict[str, Any]] = None  # {"@type":"schema:DataCatalog","schema:name":"..."}
    is_accessible_for_free: Optional[bool] = None
    publisher: Optional[Creator] = None
    funder: Optional[List[Creator]] = None

    def __init__(
        self,
        *,
        title: str,
        creators: List[Creator],
        description: str,
        keywords: Optional[List[str]] = None,
        license: str = "CC-BY-4.0",
        access_right: Optional[str] = None,
        version: Optional[str] = None,
        publication_date: Optional[str] = None,
        language: Optional[str] = "en",
        url: Optional[str] = None,
        same_as: Optional[List[str]] = None,
        identifiers: Optional[List[str]] = None,
        citation: Optional[List[Union[str, Dict[str, Any]]]] = None,
        is_based_on: Optional[List[str]] = None,
        variable_measured: Optional[List[Union[str, PropertyValue]]] = None,
        measurement_technique: Optional[Union[str, List[str]]] = None,
        spatial_coverage: Optional[str] = None,
        temporal_coverage: Optional[str] = None,
        included_in_data_catalog: Optional[Dict[str, Any]] = None,
        publisher: Optional[Creator] = None,
        funder: Optional[List[Creator]] = None,
        doi: Optional[str] = None,
        communities: Optional[List[str]] = None,
        related_identifiers: Optional[List[RelatedIdentifier]] = None,
        **extra: Any,
    ):
        self.title = title
        self.creators = creators
        self.description = description
        self.keywords = keywords or []
        self.license = license
        self.version = version
        self.publication_date = publication_date
        self.language = language
        self.url = url
        self.same_as = same_as or []
        self.identifiers = identifiers or []
        if doi:
            self.identifiers.append(doi)
        self.citation = citation or []
        self.is_based_on = is_based_on or []
        self.variable_measured = variable_measured or []
        self.measurement_technique = measurement_technique
        self.spatial_coverage = spatial_coverage
        self.temporal_coverage = temporal_coverage
        self.included_in_data_catalog = included_in_data_catalog
        self.publisher = publisher
        self.funder = funder
        # access_right maps to schema.org isAccessibleForFree when obviously open
        if access_right:
            self.is_accessible_for_free = access_right.lower() in {"open", "open access", "public"}
        else:
            self.is_accessible_for_free = None
        # Extra Zenodo-ish fields (kept for completeness)
        self.communities = communities or []
        self.related_identifiers = related_identifiers or []
        # Accept but ignore any additional fields for forward compatibility
        self._extra = extra

    def to_schemaorg_dataset(
        self,
        *,
        dataset_uri: Optional[str] = None,  # optional @id
        identifier: Optional[str] = None,  # short slug or DOI
        distributions: List[DataDownload] = (),
        context: Union[str, List[Any]] = DEFAULT_JSONLD_CONTEXT,
        extra_fields: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,  # auto variableMeasured from DataFrame
        merge_variables: bool = True,  # merge with self.variable_measured
        enforce_desc_bounds: bool = True,  # trim description to Google bounds
        max_description_len: int = 5000,
    ) -> Dict[str, Any]:
        # Description with guardrails (Google recommends ≤ 5000 chars)
        desc = self.description or ""
        if enforce_desc_bounds and len(desc) > max_description_len:
            desc = desc[:max_description_len]

        creators = [c.to_schema_org() for c in self.creators]
        dataset: Dict[str, Any] = {
            "@type": "schema:Dataset",
            "schema:name": self.title,
            "schema:description": desc,
            "schema:creator": creators,
            "schema:keywords": self.keywords or [],
            "schema:license": _license_to_url(self.license),
            "schema:inLanguage": self.language or "en",
        }
        if dataset_uri:
            dataset["@id"] = dataset_uri
        if self.url:
            dataset["schema:url"] = self.url
        if self.version:
            dataset["schema:version"] = self.version
        if self.publication_date:
            dataset["schema:datePublished"] = self.publication_date
        if self.same_as:
            dataset["schema:sameAs"] = self.same_as
        identifiers: List[str] = []
        if identifier:
            identifiers.append(identifier)
        for ident in self.identifiers:
            if ident not in identifiers:
                identifiers.append(ident)
        if identifiers:
            dataset["schema:identifier"] = identifiers if len(identifiers) > 1 else identifiers[0]
        if self.is_based_on:
            dataset["schema:isBasedOn"] = self.is_based_on
        if self.is_accessible_for_free is not None:
            dataset["schema:isAccessibleForFree"] = self.is_accessible_for_free
        if self.publisher:
            dataset["schema:publisher"] = self.publisher.to_schema_org()
        if self.funder:
            dataset["schema:funder"] = [f.to_schema_org() for f in self.funder]
        if self.temporal_coverage:
            dataset["schema:temporalCoverage"] = self.temporal_coverage
        if self.spatial_coverage:
            dataset["schema:spatialCoverage"] = self.spatial_coverage
        if self.measurement_technique:
            dataset["schema:measurementTechnique"] = self.measurement_technique
        if self.citation:
            dataset["schema:citation"] = self.citation

        # variableMeasured: explicit + inferred (if df provided) with dedup; else required-from-spec fallback.
        explicit_vm: List[Dict[str, Any]] = [
            v.to_schema_org()
            if hasattr(v, "to_schema_org")
            else {"@type": "schema:PropertyValue", "schema:name": str(v)}
            for v in (self.variable_measured or [])
        ]
        inferred_vm: List[Dict[str, Any]] = _variable_measured_from_df(df) if (df is not None) else []

        if merge_variables:
            all_vm = explicit_vm + inferred_vm
            # dedup by (name, propertyID)
            seen = set()
            dedup_vm: List[Dict[str, Any]] = []
            for it in all_vm:
                key = (it.get("schema:name"), it.get("schema:propertyID"))
                if key not in seen:
                    seen.add(key)
                    dedup_vm.append(it)
            vm_final = dedup_vm
        else:
            vm_final = explicit_vm or inferred_vm

        if not vm_final:
            vm_final = _required_pvs_from_spec()
        if not vm_final:  # last-resort emergency fallback
            vm_final = [
                PropertyValue(name="Voltage", property_id=None, unit_text="V").to_schema_org(),
                PropertyValue(name="Current", property_id=None, unit_text="A").to_schema_org(),
                PropertyValue(name="Test Time", property_id=None, unit_text="s").to_schema_org(),
            ]
        dataset["schema:variableMeasured"] = vm_final

        # Distributions
        dd_nodes: List[Dict[str, Any]] = [d.to_schema_org() for d in distributions] if distributions else []
        if dd_nodes:
            dataset["schema:distribution"] = dd_nodes

        # Inline CSVW Table(s) under mainEntity
        csvw_embedded: List[Dict[str, Any]] = []
        for d in distributions:
            t = d.to_csvw_table_embedded()
            if t:
                csvw_embedded.append(t)
        if csvw_embedded:
            dataset["schema:mainEntity"] = csvw_embedded

        if self.included_in_data_catalog:
            dataset["schema:includedInDataCatalog"] = self.included_in_data_catalog

        if extra_fields:
            dataset.update(extra_fields)

        ctx = list(context) if isinstance(context, (list, tuple)) else [context]
        return {"@context": ctx, **dataset}

    def save_jsonld(
        self,
        out_path: Union[str, Path],
        *,
        # anything accepted by to_schemaorg_dataset:
        dataset_uri: Optional[str] = None,
        identifier: Optional[str] = None,
        distributions: List[DataDownload] = (),
        context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
        extra_fields: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
        merge_variables: bool = True,
        enforce_desc_bounds: bool = True,
        max_description_len: int = 5000,
        indent: int = 2,
    ) -> Path:
        """Build and save Schema.org + CSVW JSON-LD to a file."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        obj = self.to_schemaorg_dataset(
            dataset_uri=dataset_uri,
            identifier=identifier,
            distributions=distributions,
            context=context,
            extra_fields=extra_fields,
            df=df,
            merge_variables=merge_variables,
            enforce_desc_bounds=enforce_desc_bounds,
            max_description_len=max_description_len,
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        return out_path

    def save_rich_results_html(
        self,
        out_path: Union[str, Path],
        *,
        title: Optional[str] = None,
        graphify: bool = False,
        # anything accepted by to_schemaorg_dataset:
        dataset_uri: Optional[str] = None,
        identifier: Optional[str] = None,
        distributions: List[DataDownload] = (),
        context: Union[str, List[Any]] = ("https://schema.org/", "http://www.w3.org/ns/csvw"),
        extra_fields: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
        merge_variables: bool = True,
        enforce_desc_bounds: bool = True,
        max_description_len: int = 5000,
        indent: int = 2,
    ) -> Path:
        """
        Write a minimal HTML file embedding the Dataset JSON-LD in a
        <script type="application/ld+json">. Set graphify=True to wrap the
        dataset in @graph (matches many rich-result examples).
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        obj = self.to_schemaorg_dataset(
            dataset_uri=dataset_uri,
            identifier=identifier,
            distributions=distributions,
            context=context,
            extra_fields=extra_fields,
            df=df,
            merge_variables=merge_variables,
            enforce_desc_bounds=enforce_desc_bounds,
            max_description_len=max_description_len,
        )

        if graphify:
            ctx = obj.get("@context", [])
            dataset_node = {k: v for k, v in obj.items() if k != "@context"}
            obj = {"@context": ctx, "@graph": [dataset_node]}

        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{(title or self.title)}</title>
    <script type="application/ld+json">
{json.dumps(obj, ensure_ascii=False, indent=indent)}
    </script>
  </head>
  <body></body>
</html>
"""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        return out_path


# ---------------------------
# IO helpers
# ---------------------------


def save_schemaorg_dataset(
    meta: Dataset,
    *,
    dataset_uri: Optional[str],
    identifier: Optional[str],
    distributions: List[DataDownload],
    out_path: Union[str, Path],
    context: Union[str, List[Any]] = DEFAULT_JSONLD_CONTEXT,
    extra_fields: Optional[Dict[str, Any]] = None,
    indent: int = 2,
    df: Optional[pd.DataFrame] = None,
    merge_variables: bool = True,
    enforce_desc_bounds: bool = True,
    max_description_len: int = 5000,
) -> Path:
    """
    Serialize a schema.org Dataset (with distributions), embedding CSVW Table(s) in mainEntity.
    If `df` is provided, variableMeasured is auto-populated from DataFrame headers/attrs.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    obj = meta.to_schemaorg_dataset(
        dataset_uri=dataset_uri,
        identifier=identifier,
        distributions=distributions,
        context=context,
        extra_fields=extra_fields,
        df=df,
        merge_variables=merge_variables,
        enforce_desc_bounds=enforce_desc_bounds,
        max_description_len=max_description_len,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    return out_path


# CLI-facing convenience: build JSON-LD sidecar for a single data file
def save_jsonld(
    meta: Dataset,
    data_path: Union[str, Path],
    *,
    out_path: Union[str, Path],
    dataset_uri: Optional[str] = None,
    identifier: Optional[str] = None,
    distributions: Optional[List[DataDownload]] = None,
    context: Union[str, List[Any]] = DEFAULT_JSONLD_CONTEXT,
    extra_fields: Optional[Dict[str, Any]] = None,
    df: Optional[pd.DataFrame] = None,
    merge_variables: bool = True,
    enforce_desc_bounds: bool = True,
    max_description_len: int = 5000,
    indent: int = 2,
    csvw_schema_url: Optional[str] = None,
) -> Path:
    data_path = Path(data_path)
    dist = DataDownload.for_file(data_path)
    dists = distributions or [dist]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    obj = meta.to_schemaorg_dataset(
        dataset_uri=dataset_uri,
        identifier=identifier,
        distributions=dists,
        context=context,
        extra_fields=extra_fields,
        df=df,
        merge_variables=merge_variables,
        enforce_desc_bounds=enforce_desc_bounds,
        max_description_len=max_description_len,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    return out_path


# Exported symbols
__all__ = [
    "Dataset",
    "Creator",
    "PropertyValue",
    "RelatedIdentifier",
    "Battery",
    "DataDownload",
    "save_schemaorg_dataset",
    "save_jsonld",
]
