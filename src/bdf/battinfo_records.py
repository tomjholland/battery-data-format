"""Hand-written record models mirroring BattINFO's ``test`` and ``dataset``
entity schemas.

These models are the read-metadata handoff contract: a plugin fills only the
fields it can derive, and every other declared leaf field stays ``None``. The
nesting and property names match BattINFO exactly (``meta.test_record.test.
instrument_name``, not a flattened facade), so a caller who already speaks
BattINFO finds nothing to translate.

Only the fields BDF writes are declared. ``extra="allow"`` on every model
carries any other upstream or workspace-owned field through validation and
serialisation unharmed, without BDF needing to know its shape. BDF never
imports ``battinfo`` at runtime: ``battinfo[processing]`` depends on
``batterydf``, so importing back here would make the dependency graph
cyclic. These models are a plain, independent mirror of the shapes BDF
writes into, not a fork of upstream's schema tree.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TestSection",
    "Provenance",
    "TestRecord",
    "VariableMeasured",
    "DatasetSection",
    "DatasetRecord",
    "BdfReadInfo",
    "ReadMetadata",
]


def _prune_sections(model: BaseModel, dumped: dict[str, Any]) -> dict[str, Any]:
    """Recursively drop declared section-typed fields whose dump pruned to ``{}``.

    Args:
        model: The model instance ``dumped`` was produced from, giving typed
            access to each declared field's own value.
        dumped: This model's own ``model_dump(exclude_none=True)`` mapping.

    Returns:
        ``dumped`` with each declared field that holds a model instance
        pruned in turn, its key dropped if that pruned down to ``{}``, and
        each model item of a declared list field pruned the same way.
        Only declared fields are visited: an undeclared (``extra="allow"``)
        field, even one whose value is an empty dict, is never looked at
        and so is never dropped.
    """
    result = dict(dumped)
    for field_name in type(model).model_fields:
        if field_name not in result:
            continue
        value = getattr(model, field_name)
        if isinstance(value, BaseModel):
            pruned = _prune_sections(value, result[field_name])
            if pruned:
                result[field_name] = pruned
            else:
                del result[field_name]
        elif isinstance(value, list):
            result[field_name] = [
                _prune_sections(item, item_dump) if isinstance(item, BaseModel) else item_dump
                for item, item_dump in zip(value, result[field_name], strict=True)
            ]
    return result


class _RecordModel(BaseModel):
    """Shared configuration for every BattINFO record model."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dict in BattINFO shape.

        Returns:
            The model dumped with ``exclude_none=True`` and every declared
            section field's dict pruned once it pruned down to ``{}``.
            ``None`` means unset everywhere: a field left at its default or
            explicitly assigned ``None`` is omitted, and a section left
            entirely unset vanishes rather than appear as an empty ``{}``.
        """
        return _prune_sections(self, self.model_dump(exclude_none=True))


class TestSection(_RecordModel):
    """The ``test`` section of a BattINFO test record."""

    name: str | None = None
    instrument_name: str | None = None
    started_at: int | None = None
    ended_at: int | None = None


class Provenance(_RecordModel):
    """The ``provenance`` section shared by test and dataset records."""

    source_file: str | None = None
    source_type: str | None = None


class TestRecord(_RecordModel):
    """A BattINFO test record: ``test.schema.json``."""

    schema_version: str | None = None
    test: TestSection = Field(default_factory=TestSection)
    provenance: Provenance = Field(default_factory=Provenance)


class VariableMeasured(_RecordModel):
    """One entry of ``dataset.variable_measured``."""

    name: str | None = None
    unit_text: str | None = None
    same_as: str | None = None
    description: str | None = None


class DatasetSection(_RecordModel):
    """The ``dataset`` section of a BattINFO dataset record."""

    variable_measured: list[VariableMeasured] | None = None


class DatasetRecord(_RecordModel):
    """A BattINFO dataset record: ``dataset.schema.json``."""

    schema_version: str | None = None
    dataset: DatasetSection = Field(default_factory=DatasetSection)
    provenance: Provenance = Field(default_factory=Provenance)


class BdfReadInfo(_RecordModel):
    """BDF-owned read audit facts, kept out of the BattINFO records."""

    source: str | None = None
    time_reconciliation: list[dict] | None = None


class ReadMetadata(_RecordModel):
    """The metadata a BDF read returns: the BattINFO records plus BDF's own
    read audit section."""

    test_record: TestRecord = Field(default_factory=TestRecord)
    dataset_record: DatasetRecord = Field(default_factory=DatasetRecord)
    bdf: BdfReadInfo = Field(default_factory=BdfReadInfo)
