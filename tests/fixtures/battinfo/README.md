# Pinned BattINFO fixtures

Copies of upstream entity schemas from [`BIG-MAP/BattINFO`](https://github.com/BIG-MAP/BattINFO),
taken at the commit recorded in `VERSION`.

| file | upstream path |
| --- | --- |
| `test.schema.json` | `assets/schemas/test.schema.json` |
| `dataset.schema.json` | `assets/schemas/dataset.schema.json` |
| `provenance.schema.json` | `assets/schemas/modules/common/provenance.schema.json` |

These are **test fixtures only**, used by `tests/unit/test_battinfo_contract.py`
to assert that every field path BDF's hand-written `test_record` / `dataset_record`
models write (`src/bdf/battinfo_records.py`) still exists upstream. BDF must never
import `battinfo` at runtime: `battinfo[processing]` depends on `batterydf`, and
importing back would make the dependency graph cyclic. Pinned copies let the
contract test fail loudly when an upstream rename breaks a field name BDF writes,
without taking on the dependency.

There is no bundled snapshot of BattINFO's full schema set, no refresh script, and
no pinned importer source: BDF exposes no importer adapter, so only the entity
schemas that describe the shapes BDF writes into are pinned.

## Hand-refresh procedure

There is no automated refresh script; refreshing is a manual, occasional task:

1. Pick the upstream commit (or tag) to pin, e.g. the latest release of
   `BIG-MAP/BattINFO`.
2. Re-download the three files listed above at that commit and overwrite the
   copies in this directory.
3. Update `VERSION` with the new commit SHA, its commit date, and the upstream
   `schema_version`.
4. Run `uv run pytest tests/unit/test_battinfo_contract.py -q`. A failure names
   the field path that no longer resolves upstream — fix the corresponding
   hand-written model in `src/bdf/battinfo_records.py` (rename or remove the
   field) and note the change in `CHANGELOG.md`.
