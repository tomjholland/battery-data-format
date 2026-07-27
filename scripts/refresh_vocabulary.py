"""Re-fetch the published BattINFO context and BDF ontology; diff the vendored copies.

Dependency-free by design: standard library only, so refreshing the pinned
vocabulary never needs EMMOntoPy, Owlready2, or the ``battinfo`` package. The
default is a read-only diff; ``--write`` is what actually updates the vendored
files.

Usage:
    python scripts/refresh_vocabulary.py            # report drift, exit 1 if any
    python scripts/refresh_vocabulary.py --write    # update the vendored copies
    python scripts/refresh_vocabulary.py --ontology-version 1.2.0
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from bdf.vocabulary import (  # noqa: E402
    BATTINFO_CONTEXT_PATH,
    BATTINFO_CONTEXT_URL,
    BATTINFO_VERSION,
)

_ONTOLOGY_SNAPSHOT = _REPO_ROOT / "src" / "bdf" / "data" / "bdf-ontology-snapshot.ttl"
_ONTOLOGY_URL_TMPL = (
    "https://raw.githubusercontent.com/battery-data-alliance/"
    "battery-data-format-ontology/{version}/battery-data-format.ttl"
)


def _fetch(url: str) -> bytes:
    """Fetch a URL with the standard library.

    Args:
        url: Absolute HTTP(S) URL.

    Returns:
        The response body.
    """
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URLs
        return response.read()


def _canonical_json(raw: bytes) -> str:
    """Return stably-ordered JSON text so the diff shows real changes only.

    Args:
        raw: Raw JSON bytes.

    Returns:
        Pretty-printed, key-sorted JSON text.
    """
    return json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"


def _report(name: str, current: str, fetched: str) -> bool:
    """Print a unified diff between the vendored and fetched text.

    Args:
        name: Label for the diff header.
        current: Vendored text.
        fetched: Freshly fetched text.

    Returns:
        True if the two differ.
    """
    if current == fetched:
        print(f"{name}: up to date")
        return False
    diff = list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            fetched.splitlines(keepends=True),
            fromfile=f"vendored/{name}",
            tofile=f"published/{name}",
            n=1,
        )
    )
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    print(f"{name}: DRIFT (+{added} / -{removed} lines)")
    sys.stdout.writelines(diff[:200])
    if len(diff) > 200:
        print(f"... {len(diff) - 200} more diff lines")
    return True


def main(argv: list[str] | None = None) -> int:
    """Diff, and optionally refresh, the vendored vocabulary assets.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        0 when both vendored copies match what is published, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="update the vendored copies in place")
    parser.add_argument(
        "--ontology-version",
        default="1.3.0",
        help="BDF ontology release tag to fetch (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    print(f"BattINFO context pin: {BATTINFO_VERSION}")
    print(f"  {BATTINFO_CONTEXT_URL}")

    drifted = False

    fetched_context = _canonical_json(_fetch(BATTINFO_CONTEXT_URL))
    current_context = _canonical_json(BATTINFO_CONTEXT_PATH.read_bytes())
    if _report("battinfo-context.json", current_context, fetched_context):
        drifted = True
        if args.write:
            BATTINFO_CONTEXT_PATH.write_text(fetched_context, encoding="utf-8")
            print(f"  wrote {BATTINFO_CONTEXT_PATH}")

    ontology_url = _ONTOLOGY_URL_TMPL.format(version=args.ontology_version)
    print(f"BDF ontology release: {args.ontology_version}")
    print(f"  {ontology_url}")

    # The snapshot is a re-serialisation, so it is regenerated through the same
    # code path the package uses rather than written from the fetched bytes.
    # Nothing rewrites its owl:imports: the vendored context pin follows the
    # snapshot, so the snapshot is passed through untouched.
    from bdf.spec import ColumnOntology

    if args.write:
        ColumnOntology.get_snapshot(version=args.ontology_version)
        print(f"  wrote {_ONTOLOGY_SNAPSHOT}")
    else:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            regenerated = ColumnOntology.get_snapshot(dest=Path(tmp) / "snapshot.ttl", version=args.ontology_version)
            if _report(
                "bdf-ontology-snapshot.ttl",
                _ONTOLOGY_SNAPSHOT.read_text(encoding="utf-8"),
                regenerated.read_text(encoding="utf-8"),
            ):
                drifted = True

    if drifted and not args.write:
        print("\nVendored vocabulary is out of date. Re-run with --write to refresh.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
