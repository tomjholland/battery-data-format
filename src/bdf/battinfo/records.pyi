"""Type stubs for the record attributes the bundled BattINFO schema declares.

Generated from the bundled schema documents. Nothing in ``bdf`` imports this
module at run time: a caller annotates a record field against a class here,
under ``TYPE_CHECKING``, and the type checker reads the shape from the class
alone.
"""

from typing import Any

class BattinfoTest:
    artifacts: list[dict[str, Any]] | None
    contributor: list[dict[str, Any]] | None
    funding: BattinfoFunding
    license: str | None
    notes: list[str] | None
    provenance: BattinfoProvenance
    schema_version: str | None
    test: BattinfoTest2
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunding:
    acronym: str | None
    funder: BattinfoFunder
    id: str | None
    identifier: str | None
    name: str | None
    program: str | None
    type: Any | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunder:
    name: str | None
    same_as: str | None
    type: Any | None
    url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoProvenance:
    battinfo_version: str | None
    citation: str | None
    citation_doi: str | None
    comment: str | None
    curated_by: str | None
    file_hash: str | None
    retrieved_at: int | None
    source_file: str | None
    source_name: str | None
    source_type: str | None
    source_url: str | None
    workflow_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoTest2:
    cell_id: str | None
    channel_id: str | None
    conditions: BattinfoConditions
    conformance: BattinfoConformance
    dataset_ids: list[str] | None
    description: str | None
    ended_at: int | None
    equipment_id: str | None
    handle: str | None
    id: str | None
    identifier: str | None
    instrument_name: str | None
    kind: str | None
    name: str | None
    protocol_id: str | None
    protocol_name: str | None
    protocol_url: str | None
    short_id: str | None
    started_at: int | None
    status: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoConditions:
    def __getattr__(self, name: str) -> BattinfoQuantity: ...
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoQuantity:
    co_type: str | None
    conditions: BattinfoConditions2
    max_value: float | None
    min_value: float | None
    sample_count: int | None
    standard_deviation: float | None
    typical_value: float | None
    unit: str | None
    unit_text: str | None
    value: float | str | None
    value_basis: str | None
    value_text: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoConditions2:
    def __getattr__(self, name: str) -> BattinfoQuantity: ...
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoConformance:
    deviations: list[dict[str, Any]] | None
    note: str | None
    status: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoCellInstance:
    cell_instance: BattinfoCellInstance2
    contributor: list[dict[str, Any]] | None
    datasets: list[dict[str, Any]] | None
    funding: BattinfoFunding2
    license: str | None
    measured: BattinfoSpecSet
    notes: list[str] | None
    provenance: BattinfoProvenance2
    schema_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoCellInstance2:
    batch_id: str | None
    battery_status: str | None
    cell_spec_id: str | None
    conformance: BattinfoConformance2
    counter_electrode_id: str | None
    dpp_status: str | None
    expires_at: int | str | None
    grade: str | None
    handle: str | None
    id: str | None
    manufactured_at: int | str | None
    name: str | None
    serial_number: str | None
    service_start_date: str | None
    short_id: str | None
    supersedes: str | None
    working_electrode_id: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoConformance2:
    deviations: list[dict[str, Any]] | None
    note: str | None
    status: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunding2:
    acronym: str | None
    funder: BattinfoFunder2
    id: str | None
    identifier: str | None
    name: str | None
    program: str | None
    type: Any | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunder2:
    name: str | None
    same_as: str | None
    type: Any | None
    url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoSpecSet:
    ac_internal_resistance: BattinfoSpecItem
    calendar_life: BattinfoSpecItem
    capacity_fade: BattinfoSpecItem
    capacity_threshold_exhaustion: BattinfoSpecItem
    certified_usable_energy: BattinfoSpecItem
    charging_capacity: BattinfoSpecItem
    charging_cutoff_voltage: BattinfoSpecItem
    charging_energy: BattinfoSpecItem
    charging_temperature_max: BattinfoSpecItem
    charging_temperature_min: BattinfoSpecItem
    charging_time: BattinfoSpecItem
    charging_voltage: BattinfoSpecItem
    continuous_charging_current: BattinfoSpecItem
    continuous_discharging_current: BattinfoSpecItem
    cycle_life: BattinfoSpecItem
    cycle_life_c_rate: BattinfoSpecItem
    dc_internal_resistance: BattinfoSpecItem
    diameter: BattinfoSpecItem
    discharging_capacity: BattinfoSpecItem
    discharging_cutoff_voltage: BattinfoSpecItem
    discharging_energy: BattinfoSpecItem
    discharging_temperature_max: BattinfoSpecItem
    discharging_temperature_min: BattinfoSpecItem
    electrode_area: BattinfoSpecItem
    energy_density: BattinfoSpecItem
    external_surface_area: BattinfoSpecItem
    height: BattinfoSpecItem
    impedance: BattinfoSpecItem
    initial_coulombic_efficiency: BattinfoSpecItem
    internal_resistance: BattinfoSpecItem
    length: BattinfoSpecItem
    mass: BattinfoSpecItem
    maximum_charging_temperature: BattinfoSpecItem
    maximum_continuous_charging_current: BattinfoSpecItem
    maximum_continuous_discharging_current: BattinfoSpecItem
    maximum_discharging_temperature: BattinfoSpecItem
    maximum_power: BattinfoSpecItem
    maximum_pulse_charging_current: BattinfoSpecItem
    maximum_pulse_discharging_current: BattinfoSpecItem
    maximum_storage_temperature: BattinfoSpecItem
    min_capacity: BattinfoSpecItem
    minimum_capacity: BattinfoSpecItem
    minimum_charging_temperature: BattinfoSpecItem
    minimum_discharging_temperature: BattinfoSpecItem
    minimum_storage_temperature: BattinfoSpecItem
    nominal_capacity: BattinfoSpecItem
    nominal_continuous_charging_current: BattinfoSpecItem
    nominal_continuous_discharging_current: BattinfoSpecItem
    nominal_energy: BattinfoSpecItem
    nominal_voltage: BattinfoSpecItem
    operating_temperature_max: BattinfoSpecItem
    operating_temperature_min: BattinfoSpecItem
    power_capability: BattinfoSpecItem
    power_density: BattinfoSpecItem
    power_energy_ratio: BattinfoSpecItem
    pulse_charging_current: BattinfoSpecItem
    pulse_discharging_current: BattinfoSpecItem
    rated_capacity: BattinfoSpecItem
    rated_energy: BattinfoSpecItem
    round_trip_energy_efficiency: BattinfoSpecItem
    round_trip_energy_efficiency_50pct: BattinfoSpecItem
    self_discharge_rate: BattinfoSpecItem
    specific_energy: BattinfoSpecItem
    specific_power: BattinfoSpecItem
    state_of_health: BattinfoSpecItem
    storage_temperature_max: BattinfoSpecItem
    storage_temperature_min: BattinfoSpecItem
    thickness: BattinfoSpecItem
    typical_capacity: BattinfoSpecItem
    typical_energy: BattinfoSpecItem
    upper_voltage_limit: BattinfoSpecItem
    volume: BattinfoSpecItem
    width: BattinfoSpecItem
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoSpecItem:
    co_type: str | None
    conditions: BattinfoConditions3
    max_value: float | None
    min_value: float | None
    raw: BattinfoRawExtraction
    sample_count: int | None
    standard_deviation: float | None
    typical_value: float | None
    unit: str | None
    unit_code: str | None
    unit_text: str | None
    value: float | None
    value_basis: str | None
    value_text: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoConditions3:
    def __getattr__(self, name: str) -> BattinfoQuantity: ...
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoRawExtraction:
    confidence: float | None
    page: int | None
    text: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoProvenance2:
    battinfo_version: str | None
    citation: str | None
    citation_doi: str | None
    comment: str | None
    curated_by: str | None
    file_hash: str | None
    retrieved_at: int | None
    source_file: str | None
    source_name: str | None
    source_type: str | None
    source_url: str | None
    workflow_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoChannelInstance:
    channel: BattinfoChannel
    notes: list[str] | None
    provenance: BattinfoProvenance3
    schema_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoChannel:
    comment: str | None
    equipment_id: str | None
    handle: str | None
    id: str | None
    index: int | None
    label: str | None
    property: BattinfoQuantitativeProperties
    short_id: str | None
    status: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoQuantitativeProperties:
    def __getattr__(self, name: str) -> BattinfoQuantity: ...
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoProvenance3:
    battinfo_version: str | None
    citation: str | None
    citation_doi: str | None
    retrieved_at: int | None
    source_file: str | None
    source_name: str | None
    source_type: str | None
    source_url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoEquipmentInstance:
    equipment: BattinfoEquipment
    notes: list[str] | None
    provenance: BattinfoProvenance4
    schema_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoEquipment:
    comment: str | None
    commissioned_at: int | str | None
    equipment_spec_id: str | None
    handle: str | None
    id: str | None
    location: str | None
    name: str | None
    property: BattinfoQuantitativeProperties
    serial_number: str | None
    short_id: str | None
    status: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoProvenance4:
    battinfo_version: str | None
    citation: str | None
    citation_doi: str | None
    retrieved_at: int | None
    source_file: str | None
    source_name: str | None
    source_type: str | None
    source_url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoTestProtocol:
    artifacts: list[dict[str, Any]] | None
    conditions: Any | None
    contributor: list[dict[str, Any]] | None
    facets: Any | None
    funding: BattinfoFunding3
    license: str | None
    method: list[dict[str, Any]] | None
    notes: list[str] | None
    provenance: BattinfoProvenance5
    record: BattinfoRecord
    safety: BattinfoSafety
    schema_version: str | None
    test_spec: BattinfoTestSpec
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunding3:
    acronym: str | None
    funder: BattinfoFunder3
    id: str | None
    identifier: str | None
    name: str | None
    program: str | None
    type: Any | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunder3:
    name: str | None
    same_as: str | None
    type: Any | None
    url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoProvenance5:
    battinfo_version: str | None
    citation: str | None
    citation_doi: str | None
    comment: str | None
    curated_by: str | None
    derived_from_artifact: str | None
    file_hash: str | None
    retrieved_at: int | None
    source_file: str | None
    source_name: str | None
    source_type: str | None
    source_url: str | None
    workflow_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoRecord:
    current_mA: float | None
    time_s: float | None
    voltage_V: float | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoSafety:
    delay_s: float | None
    max_capacity_mAh: float | None
    max_current_mA: float | None
    max_temperature_degC: float | None
    max_voltage_V: float | None
    min_current_mA: float | None
    min_temperature_degC: float | None
    min_voltage_V: float | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoTestSpec:
    description: str | None
    handle: str | None
    id: str | None
    identifier: str | None
    kind: str | None
    name: str | None
    protocol_url: str | None
    short_id: str | None
    version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoDatasetSchemaOrgAligned:
    contributor: list[dict[str, Any]] | None
    dataset: BattinfoDataset
    funding: BattinfoFunding4
    notes: list[str] | None
    provenance: BattinfoProvenance6
    schema_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoDataset:
    about: list[str] | None
    access_url: str | None
    additional_type: str | list[str] | None
    citations: list[dict[str, Any]] | None
    conditions_of_access: str | None
    created_at: int | None
    creators: list[dict[str, Any]] | None
    description: str | None
    distributions: list[dict[str, Any]] | None
    funders: list[dict[str, Any]] | None
    handle: str | None
    id: str | None
    identifier: BattinfoIdentifier
    in_language: str | None
    included_in_data_catalog: BattinfoIncludedInDataCatalog
    is_accessible_for_free: bool | None
    is_based_on: list[str] | None
    keywords: list[str] | None
    license: str | None
    main_entity: BattinfoMainEntity
    measurement_methods: list[str] | None
    measurement_techniques: list[str] | None
    modified_at: int | None
    name: str | None
    published_at: int | None
    publisher: BattinfoOrganization
    same_as: list[str] | None
    series_id: str | None
    short_id: str | None
    spatial_coverage: str | None
    temporal_coverage: str | None
    variable_measured: list[dict[str, Any]] | None
    version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoIdentifier:
    property_id: str | None
    value: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoIncludedInDataCatalog:
    description: str | None
    id: str | None
    name: str | None
    same_as: str | None
    type: Any | None
    url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoMainEntity:
    description: str | None
    id: str | None
    name: str | None
    table_schema: BattinfoTableSchema
    type: Any | None
    url: str | None
    table: list[dict[str, Any]] | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoTableSchema:
    columns: list[dict[str, Any]] | None
    description: str | None
    id: str | None
    name: str | None
    primary_key: str | list[str] | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoOrganization:
    name: str | None
    same_as: str | None
    type: Any | None
    url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunding4:
    acronym: str | None
    funder: BattinfoFunder4
    id: str | None
    identifier: str | None
    name: str | None
    program: str | None
    type: Any | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoFunder4:
    name: str | None
    same_as: str | None
    type: Any | None
    url: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...

class BattinfoProvenance6:
    battinfo_version: str | None
    citation: str | None
    citation_doi: str | None
    comment: str | None
    curated_by: str | None
    file_hash: str | None
    ingest_status: str | None
    quality_flags: list[str] | None
    retrieved_at: int | None
    source_dataset_key: str | None
    source_file: str | None
    source_name: str | None
    source_row: int | None
    source_sheet: str | None
    source_type: str | None
    source_url: str | None
    source_workbook_sha256: str | None
    workflow_version: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def validate(self) -> list[str]: ...
