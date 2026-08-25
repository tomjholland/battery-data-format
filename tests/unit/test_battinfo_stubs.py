"""Tests for the type-checker stubs a bundled schema refresh writes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MUTATED_SCHEMA_PATH = _REPO_ROOT / "src" / "bdf" / "data" / "battinfo" / "schemas" / "test.schema.json"


@pytest.mark.xfail(strict=True, reason="bdf.battinfo.records stub not implemented")
def test_a_type_checker_refuses_an_unknown_attribute(tmp_path: Path) -> None:
    """battinfo-records: A type checker refuses an unknown attribute."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from bdf.battinfo.records import BattinfoCellInstance\n\n"
        "def f(c: BattinfoCellInstance) -> None:\n"
        "    c.totally_unknown_attribute\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["uv", "run", "pre-commit", "run", "mypy", "--files", str(probe)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "totally_unknown_attribute" in result.stdout


@pytest.mark.xfail(strict=True, reason="scripts.update_battinfo --check-stubs not implemented")
def test_stale_stubs_fail_a_check() -> None:
    """battinfo-records: Stale stubs fail a check."""
    clean = subprocess.run(
        [sys.executable, "scripts/update_battinfo.py", "--check-stubs"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert clean.returncode == 0

    original = _MUTATED_SCHEMA_PATH.read_text(encoding="utf-8")
    try:
        mutated = json.loads(original)
        mutated["title"] = f"{mutated['title']} (changed for this test)"
        _MUTATED_SCHEMA_PATH.write_text(json.dumps(mutated, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        stale = subprocess.run(
            [sys.executable, "scripts/update_battinfo.py", "--check-stubs"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        _MUTATED_SCHEMA_PATH.write_text(original, encoding="utf-8")

    assert stale.returncode != 0
    assert "records.pyi" in (stale.stdout + stale.stderr)


@pytest.mark.xfail(strict=True, reason="src/bdf/battinfo/records.pyi not implemented")
def test_the_stubs_stay_out_of_the_run_time() -> None:
    """battinfo-records: The stubs stay out of the run time."""
    stub_path = _REPO_ROOT / "src" / "bdf" / "battinfo" / "records.pyi"
    assert stub_path.is_file()

    result = subprocess.run(
        [sys.executable, "-c", "import sys\nimport bdf\nprint('bdf.battinfo.records' in sys.modules)"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
