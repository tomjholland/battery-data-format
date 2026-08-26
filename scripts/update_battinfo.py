#!/usr/bin/env python
"""Refresh the bundled BattINFO schemas and regenerate the model package.

Usage: ``python scripts/update_battinfo.py REF``
       ``python scripts/update_battinfo.py --check [REF]``

``REF`` is a commit, tag, or branch of ``BIG-MAP/BattINFO``. The script fetches
the managed schema files at that ref, replaces the bundled copies
atomically, stamps ``VERSION`` with the commit the ref resolves to and the
upstream version constants at it, and regenerates
``bdf.battinfo.generated``. It writes each file in the canonical
form ``normalize`` states, and not in the upstream byte layout, so an upstream
commit that only reorders keys produces no diff.

``--check`` compares alone, and it exits non-zero when the bundle is stale. It
fetches the managed files at ``REF`` (``main`` by default) and reports which ones
differ from the bundle. The comparison reads parsed content, so an upstream
commit that reformats a file, or that touches nothing under ``assets/schemas/``,
reports no difference. CI runs this mode on every pull request, which is why an
unrelated upstream commit does not turn a pull request red.

``bdf.battinfo._fetch`` owns the managed paths and the transport, and this
script calls them, so the script and a run-time load name the same files.

BDF pins nine upstream schema files under ``bdf/data/battinfo/schemas/``: the
five entity schemas (``test``, ``cell-instance``, ``channel``, ``equipment``,
``test-protocol``), ``cell-instance``'s ``cell-canonical`` dependency, and the
two shared ``modules/common`` files ``channel`` and ``equipment`` depend on.
``bdf/data/battinfo/VERSION`` stamps the ref they came from.

The regeneration is ``datamodel-codegen`` with no arguments: the whole render
is declared under ``[tool.datamodel-codegen]`` in ``pyproject.toml``, and CI
verifies the committed package with ``datamodel-codegen --check``.

Nothing in ``bdf`` imports this module. The schema files ship as package data
for a reader that wants them, and the loaders here serve the contract tests.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

from bdf.battinfo._fetch import MANAGED_SCHEMA_PATHS, fetch_documents
from bdf.battinfo._pin import refresh
from bdf.battinfo.record import _is_object
from bdf.battinfo.schema import BattinfoSchema, SchemaError

_REPO = "BIG-MAP/BattINFO"
_REPO_ROOT = Path(__file__).resolve().parents[1]

_ENTITY_DOCS: list[str] = [
    "test.schema.json",
    "cell-instance.schema.json",
    "channel.schema.json",
    "equipment.schema.json",
    "test-protocol.schema.json",
    "dataset.schema.json",
]
"""The managed documents whose root node the type stubs declare a record class for."""

_LEAF_TYPES: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
}
"""JSON Schema's own type keyword, mapped to the annotation a stub leaf carries."""

COMMIT_URL_TEMPLATE = f"https://api.github.com/repos/{_REPO}/commits/{{ref}}"
"""The upstream API URL that resolves a branch, tag, or commit to a commit."""

DEFAULT_BRANCH = "main"
"""The upstream branch a check reads when the caller names no ref."""

_REFRESH_HINT = "Run scripts/update_battinfo.py REF to refresh the bundle."


def bundle_dir() -> Path:
    """Return the bundled package-data directory for the BattINFO schemas.

    Returns:
        The directory holding the ``VERSION`` stamp and the ``schemas``
        directory.
    """
    return Path(str(importlib.resources.files("bdf.data").joinpath("battinfo")))


def schema_dir() -> Path:
    """Return the directory holding the managed schema files.

    Returns:
        The generator's input directory, which holds schema files alone.
    """
    return bundle_dir() / "schemas"


def load_managed(source: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the managed schema files the model package is rendered from.

    Args:
        source: Directory holding the schema files. Defaults to the bundled
            :func:`schema_dir`.

    Returns:
        A mapping from each managed file's path relative to ``source`` (e.g.
        ``"modules/common/quantity.schema.json"``) to its parsed content.

    Raises:
        RuntimeError: The directory or a managed schema file is missing or
            unparseable.
    """
    if source is None:
        source = schema_dir()
    try:
        return {
            rel_name: json.loads((source / rel_name).read_text(encoding="utf-8")) for rel_name in MANAGED_SCHEMA_PATHS
        }
    except Exception as exc:
        raise RuntimeError(
            f"Bundled BattINFO schemas at {source} missing or unreadable: {exc}. {_REFRESH_HINT}"
        ) from exc


def load_schema(name: str, source: Path | None = None) -> dict[str, Any]:
    """Load one managed schema file by its bundle-relative name.

    Args:
        name: A key of :data:`MANAGED_SCHEMA_PATHS`, e.g. ``"test.schema.json"``.
        source: Directory holding the schema files. Defaults to the bundled
            :func:`schema_dir`.

    Returns:
        The parsed schema document.

    Raises:
        KeyError: ``name`` is not a managed schema file.
        RuntimeError: The file is missing or unparseable.
    """
    if name not in MANAGED_SCHEMA_PATHS:
        raise KeyError(f"{name!r} is not a managed BattINFO schema file")
    if source is None:
        source = schema_dir()
    try:
        return json.loads((source / name).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Bundled BattINFO schema {source / name} missing or unreadable: {exc}. {_REFRESH_HINT}"
        ) from exc


def load_version(source: Path | None = None) -> dict[str, str]:
    """Parse the ``key=value`` per line ``VERSION`` stamp beside the schemas.

    Args:
        source: Directory holding the ``VERSION`` file. Defaults to the
            bundled :func:`bundle_dir`.

    Returns:
        A mapping of stamp keys (``repo``, ``ref``, ``schema_version``,
        ``battinfo_version``) to their values.

    Raises:
        RuntimeError: The stamp is missing or unreadable.
    """
    if source is None:
        source = bundle_dir()
    try:
        lines = (source / "VERSION").read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise RuntimeError(
            f"Bundled BattINFO VERSION stamp at {source} missing or unreadable: {exc}. {_REFRESH_HINT}"
        ) from exc
    return dict(line.split("=", 1) for line in lines if "=" in line)


def fetch_schemas(ref: str) -> dict[str, bytes]:
    """Fetch the managed schema files at ``ref``.

    The package owns the paths and the transport, so the script and a
    run-time load cannot name different files.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit or tag to pin.

    Returns:
        A mapping from each file's bundle-relative path to its raw bytes.

    Raises:
        requests.HTTPError: A schema fetch failed.
        RuntimeError: Fetched content is not valid JSON, or exceeds the size
            cap the package declares.
    """
    try:
        return fetch_documents(ref)
    except SchemaError as exc:
        raise RuntimeError(str(exc)) from exc


def resolve_ref(ref: str) -> str:
    """Resolve a branch, tag, or commit to the commit it names.

    The stamp records a commit and never a branch, because a branch moves. A
    test that refetches at a branch compares against content that upstream can
    change, and a stamp that pins a commit stays true.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` branch, tag, or commit.

    Returns:
        The full commit SHA the ref names.

    Raises:
        requests.HTTPError: The resolution request failed.
        RuntimeError: The response carries no commit SHA.
    """
    import requests

    url = COMMIT_URL_TEMPLATE.format(ref=ref)
    response = requests.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    sha = response.json().get("sha")
    if not isinstance(sha, str) or not sha:
        raise RuntimeError(f"Response from {url} carries no commit SHA for ref {ref!r}")
    return sha


def compare(ref: str, source: Path | None = None) -> dict[str, bytes]:
    """Report the managed schema files that differ from the bundle at ``ref``.

    The comparison reads parsed content, not raw bytes. An upstream commit that
    only reformats a file therefore reports no difference, and neither does a
    commit that leaves ``assets/schemas/`` alone.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` branch, tag, or commit to fetch.
        source: Directory holding the bundled schema files. Defaults to the
            bundled :func:`schema_dir`.

    Returns:
        A mapping from each differing file's bundle-relative path to the bytes
        fetched at ``ref``. An empty mapping means the bundle is current.

    Raises:
        requests.HTTPError: A schema fetch failed.
        RuntimeError: Fetched content is not valid JSON, or the bundle is
            missing or unreadable.
    """
    bundled = load_managed(source)
    fetched = fetch_schemas(ref)
    return {rel_name: content for rel_name, content in fetched.items() if json.loads(content) != bundled[rel_name]}


def normalize(content: bytes) -> bytes:
    """Re-serialize JSON bytes into the repository's canonical form.

    The bundle stores every schema file with sorted keys and a two-space
    indent, so an upstream commit that only reorders keys produces no diff.
    The form matches the ``pretty-format-json`` pre-commit hook, which runs
    over these files with its default ``--indent 2`` and its default key sort.
    A bundled file therefore stays stable across a commit.

    Args:
        content: Raw JSON bytes, as fetched from upstream.

    Returns:
        The same document, serialized with sorted keys, a two-space indent,
        escaped non-ASCII characters, and a trailing newline.

    Raises:
        json.JSONDecodeError: ``content`` is not valid JSON.
    """
    text = json.dumps(json.loads(content), indent=2, sort_keys=True, ensure_ascii=True)
    return f"{text}\n".encode()


def _atomic_write(dest_dir: Path, rel_name: str, content: bytes) -> None:
    """Write ``content`` to ``dest_dir / rel_name``, replacing it atomically.

    Args:
        dest_dir: Directory to write into.
        rel_name: File name, possibly nested (e.g. ``modules/common/x.json``),
            relative to ``dest_dir``. Parent directories are created as needed.
        content: Raw bytes to write.
    """
    dest = dest_dir / rel_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False, suffix=".tmp") as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(content)
    tmp_path.replace(dest)


def write_bundle(
    fetched: dict[str, bytes],
    ref: str,
    dest: Path | None = None,
    pin: Mapping[str, str] | None = None,
) -> Path:
    """Replace the bundled schema files and stamp ``VERSION`` with the pin.

    Each schema file passes through :func:`normalize` first, so the bundle
    holds the upstream document and never the upstream byte layout.

    Args:
        fetched: The mapping :func:`fetch_schemas` returned.
        ref: The upstream ref the files came from.
        dest: Bundle directory to write into. Defaults to the bundled
            package-data directory.
        pin: The stamp to write, as :func:`bdf.battinfo._pin.refresh` returns
            it. Defaults to the repository and ``ref`` alone.

    Returns:
        The bundle directory written to.
    """
    if dest is None:
        dest = bundle_dir()
    if pin is None:
        pin = {"repo": _REPO, "ref": ref}
    schemas = dest / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    for rel_name, content in fetched.items():
        _atomic_write(schemas, rel_name, normalize(content))
    stamp = "".join(f"{key}={value}\n" for key, value in pin.items())
    _atomic_write(dest, "VERSION", stamp.encode())
    return dest


def _pascal(text: str) -> str:
    """Join a text's alphanumeric words into one PascalCase identifier.

    Args:
        text: The text to convert.

    Returns:
        The joined identifier, or an empty string where ``text`` carries no
        alphanumeric word.
    """
    words = re.split(r"[^0-9A-Za-z]+", text)
    return "".join(word[:1].upper() + word[1:] for word in words if word)


class _ClassSpec:
    """One stub class: its declared fields, and its dynamic-key fallback.

    Args:
        name: The class name.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.fields: list[tuple[str, str]] = []
        self.pattern_type: str | None = None


class _StubBuilder:
    """Walks a loaded BattINFO schema set, and collects one class per object node.

    A schema node reached through a ``$ref`` is built once and reused at every
    other point that reaches the same reference, so a shared or a recursive
    node produces one class rather than an unbounded tree. A node with no
    ``$ref`` of its own, an inline object, is built once at the point it is
    read, since nothing else can reach the same node.

    Args:
        schema: The loaded schema set to walk.
    """

    def __init__(self, schema: BattinfoSchema) -> None:
        self._schema = schema
        self._classes: dict[str, _ClassSpec] = {}
        self._identities: dict[str, str] = {}
        self._names: dict[str, str] = {}

    def build(self) -> dict[str, _ClassSpec]:
        """Build one class per object node reachable from the entity roots.

        Returns:
            Every built class, keyed by its name, in the order it was first
            reached.
        """
        for doc_name in _ENTITY_DOCS:
            doc = self._schema.docs[doc_name]
            resolved, base_id = self._schema.resolve(doc, doc["$id"])
            self._class_for(resolved, base_id, doc["$id"], self._title_name(doc.get("title", doc_name)))
        return self._classes

    def _title_name(self, title: str) -> str:
        """Turn a schema node's title into a class name.

        Args:
            title: The node's declared title.

        Returns:
            ``Battinfo``, joined to the title with its ``BattINFO`` prefix
            removed and every remaining word capitalized.
        """
        return "Battinfo" + _pascal(title.replace("BattINFO", ""))

    def _identity(self, raw_node: dict[str, Any], base_id: str) -> str:
        """Return a stable identity for a raw node, so a shared node is built once.

        Args:
            raw_node: The node as declared, before it is resolved.
            base_id: The ``$id`` ``raw_node``'s references resolve against.

        Returns:
            The node's ``$ref`` target, absolute and without a trailing empty
            fragment, where ``raw_node`` is a reference. An identity unique to
            ``raw_node`` otherwise, since an inline node reaches no other
            point in the tree.
        """
        ref = raw_node.get("$ref")
        if isinstance(ref, str):
            target = urljoin(base_id, ref)
            return target[:-1] if target.endswith("#") else target
        return f"inline:{id(raw_node)}"

    def _reserve(self, base_name: str, identity: str) -> str:
        """Reserve a class name for an identity, reusing one already assigned.

        A name already taken by a different identity gets a numeric suffix,
        counting from ``2``, until a free name is found.

        Args:
            base_name: The name to reserve, before a collision is resolved.
            identity: The identity :meth:`_identity` returned.

        Returns:
            The name reserved for ``identity``.
        """
        if identity in self._identities:
            return self._identities[identity]
        candidate = base_name
        suffix = 1
        while candidate in self._names and self._names[candidate] != identity:
            suffix += 1
            candidate = f"{base_name}{suffix}"
        self._names[candidate] = identity
        self._identities[identity] = candidate
        return candidate

    def _base_name(self, raw_node: dict[str, Any], resolved: dict[str, Any], field_hint: str) -> str:
        """Choose a class name for a node, before a collision is resolved.

        Args:
            raw_node: The node as declared, before it is resolved.
            resolved: The node, resolved and combined.
            field_hint: The field, or entry name, the node was reached through.

        Returns:
            A name built from the node's title, its reference target, or
            ``field_hint``, in that order of preference.
        """
        title = resolved.get("title")
        if isinstance(title, str) and title:
            return self._title_name(title)
        ref = raw_node.get("$ref")
        if isinstance(ref, str) and "/" in ref:
            tail = ref.rsplit("/", 1)[-1]
            if tail:
                return "Battinfo" + _pascal(tail)
        return "Battinfo" + _pascal(field_hint)

    def _class_for(self, resolved: dict[str, Any], base_id: str, identity: str, base_name: str) -> str:
        """Build a class for a resolved object node, or reuse one already built.

        Args:
            resolved: The node, resolved and combined.
            base_id: The ``$id`` ``resolved``'s references resolve against.
            identity: The node's identity, as :meth:`_identity` returns it.
            base_name: The name to reserve, before a collision is resolved.

        Returns:
            The built or reused class's name.
        """
        name = self._reserve(base_name, identity)
        if name in self._classes:
            return name
        spec = _ClassSpec(name)
        self._classes[name] = spec
        for prop_name, raw in resolved.get("properties", {}).items():
            field_type, is_object = self._type_for(raw, base_id, prop_name)
            spec.fields.append((prop_name, self._optional(field_type, is_object)))
        patterns = resolved.get("patternProperties", {})
        if patterns:
            pattern_types = [self._type_for(raw, base_id, "item")[0] for raw in patterns.values()]
            spec.pattern_type = " | ".join(dict.fromkeys(pattern_types))
        return name

    @staticmethod
    def _optional(field_type: str, is_object: bool) -> str:
        """Add ``None`` to a leaf field's annotation, unless it already carries it.

        A record never leaves an unset object field as ``None``, since a
        field whose node declares its own fields auto-constructs. A leaf
        field reads back as ``None`` while unset, unless its own node already
        names ``null`` as one of its choices.

        Args:
            field_type: The annotation :meth:`_type_for` built for the field.
            is_object: Whether the field's node is an object node.

        Returns:
            ``field_type`` unchanged for an object field, or for a leaf field
            whose annotation already carries ``None``. ``field_type`` joined
            to ``None`` otherwise.
        """
        if is_object or "None" in field_type.split(" | "):
            return field_type
        return f"{field_type} | None"

    def _type_for(self, raw_node: Any, base_id: str, field_hint: str, allow_object: bool = True) -> tuple[str, bool]:
        """Resolve one raw node to the annotation a record field carries for it.

        Args:
            raw_node: The node as declared, before it is resolved.
            base_id: The ``$id`` ``raw_node``'s references resolve against.
            field_hint: The field, or entry name, the node was reached through.
            allow_object: Whether an object node builds or reuses a class. A
                record never wraps an array's items in a nested record, so an
                object reached through an array reads back as a plain
                mapping instead.

        Returns:
            The annotation, and whether the node is an object node.
        """
        if not isinstance(raw_node, dict):
            return "Any", False
        resolved, resolved_base_id = self._schema.resolve(raw_node, base_id)
        if _is_object(resolved):
            if not allow_object:
                return "dict[str, Any]", False
            identity = self._identity(raw_node, base_id)
            base_name = self._base_name(raw_node, resolved, field_hint)
            return self._class_for(resolved, resolved_base_id, identity, base_name), True
        return self._leaf_type(resolved, resolved_base_id, field_hint), False

    def _leaf_type(self, node: dict[str, Any], base_id: str, field_hint: str) -> str:
        """Return the annotation a leaf node's own keywords carry.

        Args:
            node: The node, resolved and combined.
            base_id: The ``$id`` ``node``'s references resolve against.
            field_hint: The field, or entry name, the node was reached through.

        Returns:
            The annotation: a union across a choice's branches, a ``list``
            over its items, the type its own ``type`` keyword names, or
            ``Any`` where none of these settle it.
        """
        for keyword in ("anyOf", "oneOf"):
            subschemas = node.get(keyword)
            if subschemas:
                branch_types = [
                    self._type_for(branch, base_id, field_hint, allow_object=False)[0]
                    for branch in subschemas
                    if isinstance(branch, dict)
                ]
                return " | ".join(dict.fromkeys(branch_types)) or "Any"
        json_type = node.get("type")
        if isinstance(json_type, list):
            branch_types = [_LEAF_TYPES.get(one_type, "Any") for one_type in json_type]
            return " | ".join(dict.fromkeys(branch_types))
        if json_type == "array":
            items = node.get("items")
            item_type = (
                self._type_for(items, base_id, field_hint, allow_object=False)[0] if isinstance(items, dict) else "Any"
            )
            return f"list[{item_type}]"
        if isinstance(json_type, str):
            return _LEAF_TYPES.get(json_type, "Any")
        return "Any"


def generate_stub_text(schema: BattinfoSchema) -> str:
    """Render the type stubs a loaded schema set declares.

    Args:
        schema: The loaded schema set to render the stubs from.

    Returns:
        The ``.pyi`` module source: one class per object node reachable from
        the entity roots, and one annotation per leaf.
    """
    classes = _StubBuilder(schema).build()
    lines = [
        '"""Type stubs for the record attributes the bundled BattINFO schema declares.',
        "",
        "Generated from the bundled schema documents. Nothing in ``bdf`` imports this",
        "module at run time: a caller annotates a record field against a class here,",
        "under ``TYPE_CHECKING``, and the type checker reads the shape from the class",
        "alone.",
        '"""',
        "",
        "from typing import Any",
        "",
    ]
    for spec in classes.values():
        lines.append(f"class {spec.name}:")
        body_lines: list[str] = [f"    {field_name}: {field_type}" for field_name, field_type in spec.fields]
        if spec.pattern_type is not None:
            body_lines.append(f"    def __getattr__(self, name: str) -> {spec.pattern_type}: ...")
        body_lines.append("    def to_dict(self) -> dict[str, Any]: ...")
        body_lines.append("    def validate(self) -> list[str]: ...")
        lines.extend(body_lines)
        lines.append("")
    text = "\n".join(lines).rstrip("\n") + "\n"
    return _format_stub(text)


def _format_stub(text: str) -> str:
    """Run generated stub source through the project's own formatter.

    A committed stub is reformatted by the ``ruff-format`` pre-commit hook,
    so the generator matches that formatting itself: otherwise a freshly
    committed stub would differ from what the generator emits, and
    ``--check-stubs`` would report a stub that just passed review as stale.

    Args:
        text: The stub source, as :func:`generate_stub_text` built it.

    Returns:
        ``text``, reformatted. ``text`` unchanged where the formatter is
        unavailable or refuses the input.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--stdin-filename", "records.pyi", "-"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return text
    return result.stdout


def stub_path() -> Path:
    """Return the committed path of the generated type stubs.

    Returns:
        ``src/bdf/battinfo/records.pyi``, relative to the repository root.
    """
    return _REPO_ROOT / "src" / "bdf" / "battinfo" / "records.pyi"


def write_stub(schema: BattinfoSchema | None = None, dest: Path | None = None) -> Path:
    """Generate the type stubs and write them to ``dest``.

    Args:
        schema: The loaded schema set to render the stubs from. Defaults to
            the bundled schema set.
        dest: The file to write. Defaults to :func:`stub_path`.

    Returns:
        The file written to.
    """
    if schema is None:
        schema = BattinfoSchema(load_managed())
    if dest is None:
        dest = stub_path()
    dest.write_text(generate_stub_text(schema), encoding="utf-8")
    return dest


def check_stubs() -> list[str]:
    """Report the generated stub files that differ from the bundled schema.

    Regenerates the stubs from the bundled schema documents, with no network
    call, and compares the result against the committed file.

    Returns:
        ``["records.pyi"]`` where the committed file is missing or stale, an
        empty list where it matches what the bundled documents generate.
    """
    schema = BattinfoSchema(load_managed())
    expected = generate_stub_text(schema)
    path = stub_path()
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    return [] if current == expected else ["records.pyi"]


def regenerate() -> None:
    """Regenerate ``bdf.battinfo.generated`` from the bundled schema files.

    Runs in the repository root, because the generator reads
    ``[tool.datamodel-codegen]`` from the working directory and that section
    states the input and output paths relative to it.

    Raises:
        subprocess.CalledProcessError: The generator exited non-zero.
    """
    subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def update(ref: str, dest: Path | None = None) -> Path:
    """Fetch, stamp, and regenerate in one run.

    The version constants come first, so an upstream constant that cannot be
    read fails the run before it replaces any bundled file.

    Args:
        ref: Upstream ``BIG-MAP/BattINFO`` commit to pin. The caller resolves a
            branch or a tag first, because the stamp records a commit.
        dest: Bundle directory to write into. Defaults to the bundled
            package-data directory. The regeneration always reads the
            directory the generator configuration names, whatever ``dest``
            states.

    Returns:
        The bundle directory written to.

    Raises:
        RuntimeError: An upstream version constant is absent or unparseable at
            ``ref``.
    """
    pin = refresh(ref)
    written = write_bundle(fetch_schemas(ref), ref, dest, pin)
    regenerate()
    return written


def main(argv: list[str] | None = None) -> None:
    """Entry point: check the bundle against a ref, or refresh it at that ref.

    ``--check`` prints ``key=value`` per line, so the report names the files
    that moved:

    .. code-block:: text

        changed=true
        ref=<commit the ref resolved to>
        files=<comma-separated bundle-relative names, empty when none differ>

    Args:
        argv: Command-line arguments. Defaults to ``sys.argv[1:]``.

    Raises:
        SystemExit: ``--check`` found a managed schema file that differs from
            the bundle. CI reads that exit code, matching ``--check`` on
            ``datamodel-codegen`` and on ``scripts/generate_docs.py``.
            ``--check-stubs`` found a generated stub file that differs from
            what the bundled schema documents generate.
    """
    parser = argparse.ArgumentParser(description="Refresh or check the bundled BattINFO schemas.")
    parser.add_argument(
        "ref",
        nargs="?",
        help=f"Upstream {_REPO} branch, tag, or commit. Defaults to {DEFAULT_BRANCH} under --check.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report which managed schema files differ from the bundle, and write nothing.",
    )
    parser.add_argument(
        "--check-stubs",
        action="store_true",
        help="Regenerate the type stubs from the bundled documents and compare, with no network call.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.check_stubs:
        stale_stubs = check_stubs()
        print(f"changed={'true' if stale_stubs else 'false'}")
        print(f"files={','.join(stale_stubs)}")
        if stale_stubs:
            raise SystemExit(
                f"{len(stale_stubs)} generated stub file(s) differ from what the bundled BattINFO schema documents "
                f"generate: {', '.join(stale_stubs)}. Regenerate and commit the refreshed file."
            )
        return

    if args.check:
        resolved = resolve_ref(args.ref or DEFAULT_BRANCH)
        differing = compare(resolved)
        print(f"changed={'true' if differing else 'false'}")
        print(f"ref={resolved}")
        print(f"files={','.join(sorted(differing))}")
        if differing:
            raise SystemExit(
                f"{len(differing)} bundled BattINFO schema file(s) differ from {_REPO} at {resolved}. "
                f"Run scripts/update_battinfo.py {resolved} to refresh the bundle."
            )
        return

    requested: str | None = args.ref
    if requested is None:
        parser.error("a ref is required unless --check is given")
    resolved = resolve_ref(requested)
    update(resolved)
    print(f"BattINFO schema bundle updated: {schema_dir()} (ref={resolved})")


if __name__ == "__main__":
    main()
