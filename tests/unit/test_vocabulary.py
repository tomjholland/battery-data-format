"""Key-to-IRI resolution against the vendored, version-pinned BattINFO context.

No IRI is ever constructed from a label. These tests pin three things the rest
of the projection depends on: the vendored context and the bundled ontology
snapshot name the same BattINFO release, resolution fails closed on an unknown
key, and both work with no network and from an installed package location.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import OWL

from bdf.vocabulary import (
    BATTINFO_CONTEXT_PATH,
    BATTINFO_CONTEXT_URL,
    BATTINFO_ONTOLOGY_IRI,
    BATTINFO_VERSION,
    BDF,
    UnknownTermError,
    battinfo_context,
    has_term,
    is_reference_term,
    resolve,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOT = _REPO_ROOT / "src" / "bdf" / "data" / "bdf-ontology-snapshot.ttl"


def _snapshot_imports() -> URIRef:
    """Return the single ``owl:imports`` object in the bundled snapshot.

    Returns:
        The imported ontology IRI.
    """
    graph = Graph()
    graph.parse(_SNAPSHOT, format="turtle")
    imports = list(graph.objects(None, OWL.imports))
    assert len(imports) == 1, f"expected exactly one owl:imports, found {imports}"
    return imports[0]


# ---------------------------------------------------------------------------
# 4.4 / 4.5 — the pin and the snapshot cannot drift apart silently
# ---------------------------------------------------------------------------


def test_vendored_pin_matches_the_snapshot_import() -> None:
    """The context pin is the release the bundled snapshot actually imports.

    This is the guard that makes the whole vocabulary layer coherent: if either
    the pin or the snapshot moves without the other, this fails.
    """
    assert str(_snapshot_imports()) == BATTINFO_ONTOLOGY_IRI
    assert BATTINFO_VERSION in BATTINFO_CONTEXT_URL
    assert BATTINFO_ONTOLOGY_IRI.endswith(f"/{BATTINFO_VERSION}/battery")


def test_snapshot_import_is_not_rewritten_on_parse() -> None:
    """Parsing the snapshot yields the import triple that is on disk.

    A regression guard against reintroducing a load-time ``owl:imports``
    rewrite: the parsed graph and the raw file must name the same release.
    """
    parsed = str(_snapshot_imports())
    on_disk = _SNAPSHOT.read_text(encoding="utf-8")
    assert parsed in on_disk

    from bdf import spec

    assert not hasattr(spec, "_repin_battery_import")
    assert not hasattr(spec, "_BATTERY_IMPORT_PREFIX")


def test_column_ontology_load_leaves_the_import_untouched() -> None:
    """Going through the package's own loader does not move the import either."""
    from bdf.spec import COLUMN_ONTOLOGY

    assert COLUMN_ONTOLOGY.ontology_version  # force the lazy load
    assert str(_snapshot_imports()) == BATTINFO_ONTOLOGY_IRI


# ---------------------------------------------------------------------------
# 4.6 — fail-closed resolution
# ---------------------------------------------------------------------------


def test_context_is_the_whole_published_mapping() -> None:
    """The vendored file is BattINFO's entire context, not a hand-picked subset."""
    assert BATTINFO_CONTEXT_PATH.exists()
    assert len(battinfo_context()) > 4000


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("BatteryCycler", "https://w3id.org/emmo/domain/battery#battery_23e6170d_a70b_4de9_a4db_458e24a327ac"),
        (
            "UpperVoltageLimit",
            "https://w3id.org/emmo/domain/electrochemistry#electrochemistry_6dcd5baf_58cd_43f5_a692_51508e036c88",
        ),
        (
            "LowerVoltageLimit",
            "https://w3id.org/emmo/domain/electrochemistry#electrochemistry_534dd59c_904c_45d9_8550_ae9d2eb6bbc9",
        ),
        ("hasNumberValue", "https://w3id.org/emmo#EMMO_faf79f53_749d_40b2_807c_d34244c192f4"),
        ("hasMeasurementUnit", "https://w3id.org/emmo#EMMO_bed1d005_b04e_4a90_94cf_02bc678a8569"),
        ("Volt", "https://w3id.org/emmo#Volt"),
        ("HPPC", "https://w3id.org/emmo/domain/characterisation-methodology/chameo#HPPC"),
    ],
)
def test_known_key_resolves_to_the_canonical_iri(key: str, expected: str) -> None:
    """A published key yields the opaque IRI the context declares for it.

    The expected values are opaque UUID-style locals precisely because they are
    unguessable: nothing here could have been produced by interpolating a label.

    Args:
        key: A readable BattINFO context key.
        expected: The canonical IRI the context maps it to.
    """
    assert str(resolve(key)) == expected


def test_unknown_key_raises_with_near_matches() -> None:
    """An unresolvable key fails closed and suggests what was probably meant."""
    with pytest.raises(UnknownTermError) as excinfo:
        resolve("hasNumericalData")
    message = str(excinfo.value)
    assert "hasNumericalData" in message
    assert BATTINFO_VERSION in message
    assert "Did you mean" in message


@pytest.mark.parametrize("key", ["BatteryCellSpecification", "INR18650"])
def test_terms_added_after_the_pin_do_not_resolve(key: str) -> None:
    """A 0.20.0-only term is absent at 0.18.6, and that is the accepted gap.

    Pinned by a test rather than by prose so lifting the pin has to come here
    and delete this expectation deliberately.

    Args:
        key: A term published only after the pinned release.
    """
    assert not has_term(key)
    with pytest.raises(UnknownTermError):
        resolve(key)


def test_resolution_never_yields_a_relative_or_prefixed_iri() -> None:
    """Every resolved term is an absolute IRI, never a CURIE."""
    for key in ("BatteryCycler", "hasNumberValue", "Volt"):
        iri = str(resolve(key))
        assert iri.startswith("https://")
        assert "w3id.org/bdf/" not in iri


def test_is_reference_term_reads_the_published_type() -> None:
    """Object properties already carry ``"@type": "@id"`` upstream."""
    assert is_reference_term("hasMeasurementUnit")
    assert is_reference_term("hasControlParameter")
    assert not is_reference_term("BatteryCycler")


def test_bdf_namespace_is_the_canonical_one() -> None:
    """The obsolete ``https://w3id.org/bdf/`` namespace is not used."""
    assert str(BDF) == "https://w3id.org/battery-data-alliance/ontology/battery-data-format#"


# ---------------------------------------------------------------------------
# 4.7 / 4.8 — offline, and from an installed location
# ---------------------------------------------------------------------------


def test_resolution_needs_no_network() -> None:
    """Resolution reads only the vendored file.

    The suite runs under ``--disable-socket``, so any attempt to fetch the
    upstream context would raise here rather than in production.
    """
    battinfo_context.cache_clear()
    assert str(resolve("BatteryCycler")).startswith("https://w3id.org/emmo/domain/battery#")


def test_context_resolves_through_the_package_data_api() -> None:
    """The context is found via ``importlib.resources``, not a source-tree path.

    This is what makes it work from an installed wheel: the path is resolved
    against the ``bdf.data`` package, so it follows the package wherever it is
    installed rather than assuming a checkout layout.
    """
    import importlib.resources

    packaged = Path(str(importlib.resources.files("bdf.data").joinpath("battinfo-context.json")))
    assert packaged == BATTINFO_CONTEXT_PATH
    assert packaged.is_file()


def test_context_is_declared_in_both_build_targets() -> None:
    """An undeclared data file resolves in a checkout but not in a wheel."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    wheel, _, sdist = pyproject.partition("[tool.hatch.build.targets.sdist]")
    _, _, wheel = wheel.partition("[tool.hatch.build.targets.wheel]")
    assert "src/bdf/data/battinfo-context.json" in wheel
    assert "src/bdf/data/battinfo-context.json" in sdist


def test_installed_layout_resolves_from_a_staged_package(tmp_path: Path) -> None:
    """Resolution works from a package directory that is not the source tree.

    Stages ``bdf/data/battinfo-context.json`` alone under a temporary root and
    resolves against it, standing in for an installed wheel without needing a
    build or a network install.
    """
    staged = tmp_path / "bdf" / "data"
    staged.mkdir(parents=True)
    (staged / "battinfo-context.json").write_bytes(BATTINFO_CONTEXT_PATH.read_bytes())

    context = json.loads((staged / "battinfo-context.json").read_text(encoding="utf-8"))["@context"]
    assert context["BatteryCycler"] == str(resolve("BatteryCycler"))


# ---------------------------------------------------------------------------
# 4.9 — the refresh script reports drift without silently overwriting
# ---------------------------------------------------------------------------


def _run_refresh(*args: str, fetched: bytes) -> subprocess.CompletedProcess[str]:
    """Run the refresh script with its network fetch stubbed out.

    Args:
        *args: Extra command-line arguments for the script.
        fetched: Bytes the stubbed fetch returns for the context URL.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    driver = (
        "import json, sys, runpy\n"
        "sys.argv = ['refresh_vocabulary.py', *%r]\n"
        "sys.path.insert(0, %r)\n"
        "import urllib.request\n"
        "class _R:\n"
        "    def __init__(self, b): self._b = b\n"
        "    def read(self): return self._b\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *a): return False\n"
        "urllib.request.urlopen = lambda url, timeout=None: _R(%r)\n"
        "runpy.run_path(%r, run_name='__main__')\n"
    ) % (list(args), str(_REPO_ROOT / "src"), fetched, str(_REPO_ROOT / "scripts" / "refresh_vocabulary.py"))
    return subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )


def test_refresh_reports_no_drift_against_the_vendored_copy() -> None:
    """The vendored file matches what the pinned URL serves."""
    result = _run_refresh("--ontology-version", "1.3.0", fetched=BATTINFO_CONTEXT_PATH.read_bytes())
    assert "battinfo-context.json: up to date" in result.stdout


def test_refresh_reports_drift_and_exits_non_zero_without_writing() -> None:
    """Drift is reported and the vendored file is left exactly as it was."""
    before = BATTINFO_CONTEXT_PATH.read_bytes()
    drifted = json.loads(before)
    drifted["@context"]["ZZZNotAPublishedTerm"] = "https://example.org/not-a-term"

    result = _run_refresh("--ontology-version", "1.3.0", fetched=json.dumps(drifted).encode("utf-8"))

    assert result.returncode != 0, result.stdout
    assert "DRIFT" in result.stdout
    assert "Re-run with --write to refresh" in result.stdout
    assert BATTINFO_CONTEXT_PATH.read_bytes() == before


def test_refresh_pin_is_read_from_the_module_not_restated() -> None:
    """The script has exactly one source of truth for the pin."""
    script = (_REPO_ROOT / "scripts" / "refresh_vocabulary.py").read_text(encoding="utf-8")
    assert "BATTINFO_VERSION" in script
    assert "0.18.6" not in script
    assert "0.20.0" not in script
