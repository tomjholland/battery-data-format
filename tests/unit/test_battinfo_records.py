"""Unit tests for the hand-written BattINFO record models (``bdf.battinfo_records``).

Cover partial fill, assignment type-checking, unknown-field passthrough,
nested assignment on a fresh :class:`ReadMetadata`, and lossless ``to_dict()``
round-tripping of a curated record's non-null key set.

Imports of ``bdf.battinfo_records`` are made inside each test body rather
than at module level.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_partial_fill_validates_and_leaves_other_leaf_fields_none() -> None:
    """A record stating only one field validates; every other declared leaf field is None."""
    from bdf.battinfo_records import TestSection

    section = TestSection(started_at=1700000000)

    assert section.started_at == 1700000000
    assert section.name is None
    assert section.instrument_name is None
    assert section.ended_at is None


def test_partial_fill_on_a_nested_record_leaves_sibling_sections_empty() -> None:
    """Partial fill nested inside a TestRecord still auto-constructs the unset sibling section."""
    from bdf.battinfo_records import TestRecord, TestSection

    record = TestRecord(test=TestSection(started_at=1700000000))

    assert record.test.started_at == 1700000000
    assert record.schema_version is None
    assert record.provenance.source_file is None
    assert record.provenance.source_type is None


def test_assignment_is_type_checked() -> None:
    """Assigning a non-integer to started_at raises a validation error, not a silent coercion."""
    from bdf.battinfo_records import TestSection

    section = TestSection()

    with pytest.raises(ValidationError):
        section.started_at = "not-a-timestamp"  # type: ignore[assignment]


def test_unknown_fields_survive_validation_and_reappear_on_serialisation() -> None:
    """extra='allow' carries a field the model does not declare through unharmed."""
    from bdf.battinfo_records import TestSection

    section = TestSection.model_validate({"started_at": 1700000000, "totally_unknown_field": "kept"})

    assert section.totally_unknown_field == "kept"  # type: ignore[attr-defined]
    assert section.to_dict()["totally_unknown_field"] == "kept"


def test_nested_assignment_on_a_fresh_read_metadata_reaches_the_leaf() -> None:
    """meta.test_record.test.started_at = ... works with no None guard on a fresh ReadMetadata()."""
    from bdf.battinfo_records import ReadMetadata

    meta = ReadMetadata()
    meta.test_record.test.started_at = 1700000000

    assert meta.test_record.test.started_at == 1700000000
    assert meta.to_dict() == {"test_record": {"test": {"started_at": 1700000000}}}


def test_to_dict_round_trip_preserves_a_curated_test_records_non_null_key_set() -> None:
    """A stated null is unset (omitted), unknown fields pass through, schema_version is never injected."""
    from bdf.battinfo_records import TestRecord

    curated = {
        "test": {
            "name": "Hydra.0b_C_GITTOCV_002b",
            "instrument_name": "BCS-815 (SN 0533)",
            "started_at": 1669820421,
            "ended_at": None,  # stated null == unset: must not survive the round trip
            "cell_id": "cell-42",  # workspace-owned field the model does not declare
        },
        "provenance": {"source_file": "cell.txt"},
    }

    record = TestRecord.model_validate(curated)

    assert record.to_dict() == {
        "test": {
            "name": "Hydra.0b_C_GITTOCV_002b",
            "instrument_name": "BCS-815 (SN 0533)",
            "started_at": 1669820421,
            "cell_id": "cell-42",
        },
        "provenance": {"source_file": "cell.txt"},
    }
    assert "schema_version" not in record.to_dict()


def test_extra_empty_dict_survives_to_dict_unpruned() -> None:
    """An undeclared field holding {} survives to_dict(), unlike a declared section that dumps to {}."""
    from bdf.battinfo_records import BdfReadInfo, ReadMetadata

    meta = ReadMetadata.model_validate(
        {
            "bdf": {
                "source": "arbin_res",
                "time_reconciliation": [{"repaired": True, "detail": {}}],
                "workspace_owned_extra": {},
            }
        }
    )

    assert isinstance(meta.bdf, BdfReadInfo)
    assert meta.bdf.workspace_owned_extra == {}  # type: ignore[attr-defined]
    dumped = meta.to_dict()

    assert dumped["bdf"]["workspace_owned_extra"] == {}
    assert dumped["bdf"]["time_reconciliation"] == [{"repaired": True, "detail": {}}]
    assert "test_record" not in dumped
    assert "dataset_record" not in dumped


def test_to_dict_round_trip_preserves_a_curated_dataset_records_non_null_key_set() -> None:
    """The same round-trip guarantee holds for the dataset side, including a stated variable_measured list."""
    from bdf.battinfo_records import DatasetRecord

    curated = {
        "dataset": {
            "variable_measured": [
                {"name": "Voltage / V", "unit_text": "V", "same_as": "https://w3id.org/emmo/voltage"},
                {"name": "vendor_specific_column"},
            ],
            "license": "CC-BY-4.0",  # workspace-owned field the model does not declare
        },
        "provenance": {"source_type": "measurement"},
    }

    record = DatasetRecord.model_validate(curated)

    assert record.to_dict() == curated
    assert "schema_version" not in record.to_dict()
