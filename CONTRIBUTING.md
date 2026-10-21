# Contributing

Thanks for helping improve BDF!

## Ways to help
- Report bugs and feature requests via issues.
- Improve docs and examples.
- Add/extend cycler plugins and tests.

## Setup

Choose one workflow. **uv** is recommended as this project includes a uv.lock file which
fixes all dependencies; venv/pip is standard if you prefer traditional tooling.

### With uv (recommended)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then sync dependencies:
```bash
uv sync --all-extras
```

Install and run pre-commit:
```bash
uv run pre-commit install
uv run pre-commit run --all-files  # Manual run; auto-runs on git commit
```

Run tests and build docs:
```bash
uv run --all-extras pytest
# --all-extras will ensure your test runs in an environment with all optional dependencies
uv run sphinx-build -b html docs docs/_build/html
```

### With venv and pip

Create and activate virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate      # Windows
```

Install dependencies (requires pip>=25.1 for `--group`):
```bash
python -m pip install -e . --group dev --group docs
```

Install and run pre-commit:
```bash
pre-commit install
pre-commit run --all-files  # Manual run; auto-runs on git commit
```

Run tests and build docs:
```bash
pytest
sphinx-build -b html docs docs/_build/html
```

### Key difference

- **uv**: Environment auto-managed; `uv run CMD` executes CMD within it.
- **venv/pip**: Manually activate `.venv/` each session; run commands normally.

## Pull requests
- Keep PRs focused and include tests for new behavior.
- Update docs/README when changing user-facing APIs.
- Follow the CODE_OF_CONDUCT.

## Ontology-derived content (do not edit by hand)

The [BDF ontology](https://github.com/battery-data-alliance/battery-data-format-ontology)
is the single source of truth for the canonical quantities. The bundled
snapshot (`src/bdf/data/bdf-ontology-snapshot.ttl`) is pinned to an ontology
release, and the term tables in `README.md` (between `BEGIN/END GENERATED`
markers) are generated from it:

- To change a term's name, definition, obligation, or any other metadata,
  open a PR on the **ontology repo** — not here. A daily workflow
  (`sync-ontology.yml`) opens a PR in this repo when a new ontology release
  is published.
- After changing the snapshot locally, regenerate the tables with
  `python scripts/generate_docs.py`. CI fails if the generated regions are
  out of sync (`--check`).
- The required-column set used by `validate_df()` derives from the
  ontology's `obligation` annotations and is pinned by
  `tests/unit/test_spec_ontology_fields.py`; an ontology release that
  changes it must update that test deliberately in the sync PR.

The Supported Plugins page (`docs/plugins.rst`, between `BEGIN/END GENERATED`
markers) is generated from `bdf.plugins.PLUGINS` by the `docs/_ext/generate_plugins_doc.py`
Sphinx extension, which regenerates it automatically on every docs build. No
manual step or CI check is needed -- just change the plugin definition and
build the docs.

## BattINFO-derived content (do not edit by hand)

[BattINFO](https://github.com/BIG-MAP/BattINFO) is the source of truth for the
managed schema files under `src/bdf/data/battinfo/schemas/` (nine today), and
`src/bdf/data/battinfo/VERSION` names the upstream release they are a snapshot
of. The bundle pins published releases only. The `Metadata` records are
bounded by the loaded schema at run time, so no code is generated from these
files. They are the default set a user can replace
(`BATTINFO_SCHEMA.load_version`, `load_dir`, as for the BDF ontology). A read
checks a sidecar against the release it names (`BattinfoSchema.for_version`),
so tests that read sidecars naming another release use a release cache under
`BDF_CACHE_DIR` rather than the network.

- To change a schema, open a PR on the **BattINFO repo**, not here.
- To move the bundle to another release, run `bdf-update-battinfo VERSION`
  (for example `bdf-update-battinfo 0.8.0`) and commit the result, as
  `bdf-update-snapshot VERSION` does for the ontology. Review that diff: an
  upstream rename can break a field path the vendor parsers write
  (`src/bdf/metadata_targets.py`).
- A `freshness` test (`test_bundled_snapshot_matches_its_declared_release`)
  refetches the release `VERSION` names and fails if the bundle differs from
  it. CI runs the `freshness` tests once, in the `freshness-tests` job; run
  them locally with `uv run --all-extras pytest -m freshness`.
- To add an entity record, add its field and document to `_ENTITY_DOCS` in
  `src/bdf/metadata.py` and declare the field on `Metadata`.

## Reference artifacts are byte-exact

The files under `docs/examples/reference/` are hashed into `datasets.json`, so `.gitattributes` marks them `-text` and git must never rewrite their line endings. After pulling the change that introduced this, a pre-existing checkout may show all of them as modified and fail the manifest sha test: nothing is wrong with the data - refresh the working copies with `git checkout -- docs/examples/reference`.

## Release workflow (summary)
- Ensure CI is green (lint/type/tests/docs/build).
- Bump version in `pyproject.toml` and update `CHANGELOG.md`.
- Tag the release. A `v*` tag builds and publishes to PyPI from CI.
- Release candidates are published to PyPI as well, as pre-releases.
  `pip` and `uv` skip pre-releases unless asked, so testers opt in with
  `--pre` or an exact pin. TestPyPI is only for rehearsing the workflow.
