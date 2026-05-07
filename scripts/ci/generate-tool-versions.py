#!/usr/bin/env python3
"""Generate ``lintro/_generated_versions.py`` and sync ``manifest.json`` versions.

Single writer for all tool-version artifacts derived from ``package.json`` and
``pyproject.toml``. The seed mapping at ``lintro/_tool_packages.py`` declares
which packages are tools (and which `ToolName` they own) and which are
companions.

Modes:
    default: write outputs, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 if outputs are already in sync, exit 2 on input error.

Stdlib-only on purpose: this script runs inside Renovate's container after
``postUpgradeTasks`` so it must not require pip-installed dependencies.
Requires Python 3.11+ for ``tomllib``.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT_ERROR = 2

REPO_ROOT = Path(__file__).resolve().parents[2]

SEED_PATH = REPO_ROOT / "lintro" / "_tool_packages.py"
TOOL_VERSIONS_PATH = REPO_ROOT / "lintro" / "_tool_versions.py"
PACKAGE_JSON_PATH = REPO_ROOT / "package.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MANIFEST_PATH = REPO_ROOT / "lintro" / "tools" / "manifest.json"
GENERATED_PATH = REPO_ROOT / "lintro" / "_generated_versions.py"

GENERATED_HEADER = '''\
"""Auto-generated tool versions. Do not edit by hand.

Run ``python3 scripts/ci/generate-tool-versions.py`` to regenerate.

Sources:
    - package.json (npm devDependencies)
    - pyproject.toml (pypi dependency tables)
    - lintro/_tool_packages.py (seed mapping)
"""
'''

# Specifier pattern for ``pkg[extras]>=version`` style PEP 508 strings.
_SPEC_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)"
    r"(?:\[[^\]]+\])?"
    r"\s*(?:(?P<op>>=|==|~=|!=|>|<=|<)\s*(?P<version>[0-9][^,;\s]*))?"
    r"(?:\s*[,;].*)?$",
)

# Strict exact-version pattern for npm specs after ``^``/``~`` stripping.
# Accepts ``X.Y.Z`` and ``X.Y.Z-pre`` / ``X.Y.Z+build`` SemVer suffixes.
_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


class GenerationError(Exception):
    """Raised on unrecoverable input errors. Exits with EXIT_INPUT_ERROR."""


class Seed(NamedTuple):
    """Parsed seed mapping from ``lintro/_tool_packages.py``.

    Attributes:
        npm_owners: Mapping of npm package name -> ToolName member name or None.
        pypi_owners: Mapping of pypi package name -> ToolName member name or None.
    """

    npm_owners: dict[str, str | None]
    pypi_owners: dict[str, str | None]


def parse_seed(path: Path) -> Seed:
    """Parse the seed mapping without importing lintro.

    AST-walks ``_tool_packages.py`` for the ``NPM_PACKAGE_OWNERS`` and
    ``PYPI_PACKAGE_OWNERS`` assignments and extracts package name to
    ToolName-attribute pairs. ``None`` values are preserved as ``None``.

    Args:
        path: Path to ``lintro/_tool_packages.py``.

    Returns:
        Parsed seed.

    Raises:
        GenerationError: If the seed file is missing or malformed.
    """
    if not path.exists():
        raise GenerationError(f"seed file not found: {path}")

    tree = ast.parse(path.read_text())
    npm: dict[str, str | None] | None = None
    pypi: dict[str, str | None] | None = None

    for node in ast.walk(tree):
        target_name = _extract_assign_target(node)
        if target_name == "NPM_PACKAGE_OWNERS":
            npm = _extract_owner_mapping(node)
        elif target_name == "PYPI_PACKAGE_OWNERS":
            pypi = _extract_owner_mapping(node)

    if npm is None or pypi is None:
        raise GenerationError(
            "seed must define both NPM_PACKAGE_OWNERS and PYPI_PACKAGE_OWNERS",
        )

    return Seed(npm_owners=npm, pypi_owners=pypi)


def _extract_assign_target(node: ast.AST) -> str | None:
    """Return the target name of a top-level dict assignment, else None."""
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    return None


def _extract_owner_mapping(node: ast.AST) -> dict[str, str | None]:
    """Extract ``{str: ToolName.X | None}`` literal from an Assign/AnnAssign."""
    value = node.value if isinstance(node, ast.AnnAssign | ast.Assign) else None
    if not isinstance(value, ast.Dict):
        raise GenerationError(
            "seed mappings must be dict literals",
        )

    result: dict[str, str | None] = {}
    for key_node, val_node in zip(value.keys, value.values, strict=True):
        if not isinstance(key_node, ast.Constant) or not isinstance(
            key_node.value,
            str,
        ):
            raise GenerationError("seed dict keys must be string literals")
        package = key_node.value

        if isinstance(val_node, ast.Constant) and val_node.value is None:
            result[package] = None
        elif (
            isinstance(val_node, ast.Attribute)
            and isinstance(val_node.value, ast.Name)
            and val_node.value.id == "ToolName"
        ):
            result[package] = val_node.attr
        else:
            raise GenerationError(
                f"seed dict values must be ToolName.X or None "
                f"(got {ast.dump(val_node)})",
            )

    return result


def read_package_json(
    path: Path,
    strict_packages: set[str] | None = None,
) -> dict[str, str]:
    """Read npm package versions from ``package.json``.

    Returns a flattened dict combining ``dependencies`` and ``devDependencies``.
    Strips leading ``^``/``~`` from version specifiers. Packages listed in
    ``strict_packages`` are validated to be pinned to an exact ``X.Y.Z``
    SemVer (with an optional pre-release/build suffix); anything else
    (``>=1.0.0``, ``*``, ``latest``, ``git+...``, ``file:...``,
    ``workspace:*``, ``npm:foo@1.0.0``) raises ``GenerationError``.

    Args:
        path: Path to ``package.json``.
        strict_packages: Packages that must use an exact pin. Pass the seed's
            npm owner set so non-seeded devDependencies remain unrestricted.

    Returns:
        Mapping of package name -> exact version string.

    Raises:
        GenerationError: If the file is missing, malformed, or a strict
            package has a non-exact spec.
    """
    if not path.exists():
        raise GenerationError(f"package.json not found: {path}")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise GenerationError(f"package.json is not valid JSON: {exc}") from exc

    deps = data.get("dependencies", {}) or {}
    dev_deps = data.get("devDependencies", {}) or {}
    if not isinstance(deps, dict) or not isinstance(dev_deps, dict):
        raise GenerationError("package.json dependencies must be objects")

    strict = strict_packages or set()
    cleaned: dict[str, str] = {}
    for pkg, raw in {**deps, **dev_deps}.items():
        spec = str(raw)
        stripped = spec.lstrip("^~")
        if pkg in strict and not _EXACT_VERSION_RE.match(stripped):
            raise GenerationError(
                f"npm package '{pkg}' must be pinned to an exact X.Y.Z "
                f"version in package.json (got {spec!r}). Update the pin or "
                f"remove the package from lintro/_tool_packages.py.",
            )
        cleaned[pkg] = stripped
    return cleaned


def read_pyproject_versions(path: Path, packages: set[str]) -> dict[str, str]:
    """Read exact versions for the given pypi packages from ``pyproject.toml``.

    Scans every dependency-list table in pyproject.toml (``project.dependencies``,
    ``project.optional-dependencies.*``, ``dependency-groups.*``,
    ``tool.uv.sources``-adjacent ``dependencies`` lists). When the same package
    appears in multiple tables, all occurrences must declare the same version
    or generation fails.

    Args:
        path: Path to ``pyproject.toml``.
        packages: Set of package names to extract.

    Returns:
        Mapping of package name -> version string.

    Raises:
        GenerationError: If a seeded package is missing, has no version pin, or
            has inconsistent versions across tables.
    """
    if not path.exists():
        raise GenerationError(f"pyproject.toml not found: {path}")

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise GenerationError(f"pyproject.toml is not valid TOML: {exc}") from exc

    found: dict[str, set[str]] = {pkg: set() for pkg in packages}

    for spec in _collect_dep_strings(data):
        match = _SPEC_RE.match(spec)
        if match is None:
            continue
        name = match.group("name")
        version = match.group("version")
        if name in packages and version:
            found[name].add(version)

    versions: dict[str, str] = {}
    for pkg, observed in found.items():
        if not observed:
            raise GenerationError(
                f"pypi package '{pkg}' from seed not found with a version pin "
                f"in {_display_path(path)}",
            )
        if len(observed) > 1:
            raise GenerationError(
                f"pypi package '{pkg}' has inconsistent versions across "
                f"pyproject.toml tables: {sorted(observed)}. "
                f"Pin one canonical version.",
            )
        versions[pkg] = next(iter(observed))

    return versions


def _display_path(path: Path) -> str:
    """Format a path relative to ``REPO_ROOT`` if possible, else absolute."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _collect_dep_strings(data: dict[str, Any]) -> list[str]:
    """Collect PEP 508 strings from known pyproject dependency tables.

    Reads only the tables that conventionally hold dependency specifiers:
    ``project.dependencies``, ``project.optional-dependencies.<group>``,
    ``dependency-groups.<group>``, ``tool.uv.constraint-dependencies``, and
    ``tool.uv.override-dependencies``. Non-string entries (e.g. PEP 735
    ``include-group`` tables) are skipped.

    Args:
        data: Parsed pyproject.toml structure.

    Returns:
        Flat list of dependency specifier strings.
    """
    out: list[str] = []

    project = data.get("project")
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            out.extend(item for item in deps if isinstance(item, str))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    out.extend(item for item in group if isinstance(item, str))

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, list):
                out.extend(item for item in group if isinstance(item, str))

    tool = data.get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    if isinstance(uv, dict):
        for key in ("constraint-dependencies", "override-dependencies"):
            entries = uv.get(key)
            if isinstance(entries, list):
                out.extend(item for item in entries if isinstance(item, str))

    return out


def render_generated_module(
    npm_versions: dict[str, str],
    pypi_versions: dict[str, str],
) -> str:
    """Render the contents of ``_generated_versions.py``.

    Output is formatter-idempotent: passes black, ruff, and prettier without
    modification. Keys are sorted so the output is byte-stable across runs on
    different filesystems.

    Args:
        npm_versions: Package name -> version, npm tools and companions.
        pypi_versions: Package name -> version, pypi tools.

    Returns:
        Full module source text, terminated with a single trailing newline.
    """
    parts: list[str] = [GENERATED_HEADER, "\n"]
    parts.append("NPM_VERSIONS: dict[str, str] = {\n")
    for pkg in sorted(npm_versions):
        parts.append(f'    "{pkg}": "{npm_versions[pkg]}",\n')
    parts.append("}\n\n")
    parts.append("PYPI_VERSIONS: dict[str, str] = {\n")
    for pkg in sorted(pypi_versions):
        parts.append(f'    "{pkg}": "{pypi_versions[pkg]}",\n')
    parts.append("}\n")
    return "".join(parts)


def read_binary_tool_versions(path: Path) -> dict[str, str]:
    """Extract ``TOOL_VERSIONS`` from ``_tool_versions.py`` without importing.

    AST-walks the file for the ``TOOL_VERSIONS`` assignment, then parses each
    ``ToolName.X: "ver"`` entry. Used as the single source for non-npm/non-pypi
    tools (binary, cargo, rustup) when populating the manifest.

    Args:
        path: Path to ``lintro/_tool_versions.py``.

    Returns:
        Mapping of manifest tool name (lowercased ToolName attr) -> version.

    Raises:
        GenerationError: If the file is missing or ``TOOL_VERSIONS`` cannot be
            located.
    """
    if not path.exists():
        raise GenerationError(f"_tool_versions.py not found: {path}")

    content = path.read_text()
    tree = ast.parse(content)
    block: ast.Assign | ast.AnnAssign | None = None
    for node in ast.walk(tree):
        if _extract_assign_target(node) == "TOOL_VERSIONS":
            block = node  # type: ignore[assignment]
            break
    if block is None:
        raise GenerationError("TOOL_VERSIONS assignment not found")

    lines = content.splitlines(keepends=True)
    block_text = "".join(lines[block.lineno - 1 : block.end_lineno])

    versions: dict[str, str] = {}
    for match in re.finditer(r'ToolName\.(\w+)\s*:\s*"([^"]+)"', block_text):
        versions[match.group(1).lower()] = match.group(2)
    return versions


def build_target_versions(
    manifest_data: dict[str, Any],
    npm_versions: dict[str, str],
    pypi_versions: dict[str, str],
    binary_versions: dict[str, str],
) -> dict[str, str]:
    """Resolve each manifest entry's expected version from the right source.

    ``install.type`` determines the source: ``npm``/``pip`` look up the
    ``install.package`` in the relevant generated dict; everything else
    (``binary``, ``cargo``, ``rustup``) is matched against ``binary_versions``
    by manifest entry name.

    Args:
        manifest_data: Parsed manifest.json content.
        npm_versions: Package -> version for npm packages.
        pypi_versions: Package -> version for pypi packages.
        binary_versions: Manifest-name -> version for non-npm/non-pypi tools.

    Returns:
        Mapping of manifest entry name -> desired version.
    """
    targets: dict[str, str] = {}
    for entry in manifest_data.get("tools", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        install = entry.get("install") or {}
        install_type = install.get("type") if isinstance(install, dict) else None
        package = install.get("package") if isinstance(install, dict) else None

        if (
            install_type == "npm"
            and isinstance(package, str)
            and package in npm_versions
        ):
            targets[name] = npm_versions[package]
        elif (
            install_type == "pip"
            and isinstance(package, str)
            and package in pypi_versions
        ):
            targets[name] = pypi_versions[package]
        elif name in binary_versions:
            targets[name] = binary_versions[name]
    return targets


def render_manifest(current_text: str, target_versions: dict[str, str]) -> str:
    """Apply targeted ``version`` updates to ``manifest.json`` text.

    Edits only the ``"version": "..."`` field that immediately follows each
    target tool's ``"name"`` field. All other bytes of the file (whitespace,
    key order, inline-array formatting) are preserved — round-tripping
    through ``json.dumps`` would reflow inline arrays into a noisy diff.

    Args:
        current_text: Current manifest.json contents.
        target_versions: Mapping of manifest entry name -> desired version.

    Returns:
        New manifest.json text.

    Raises:
        GenerationError: If the manifest is malformed, or if a target name
            has its ``version`` field separated from its ``name`` sibling by
            intervening keys, or appears more than once.
    """
    try:
        json.loads(current_text)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"manifest.json is not valid JSON: {exc}") from exc

    text = current_text
    for name, version in target_versions.items():
        pattern = re.compile(
            rf'("name":\s*"{re.escape(name)}",\s*"version":\s*")[^"]+(")',
        )
        text, count = pattern.subn(rf"\g<1>{version}\g<2>", text)
        if count == 0:
            raise GenerationError(
                f"manifest.json has no '{name}' entry with adjacent name/version "
                f"fields. Add the entry, or restore the conventional "
                f"name-then-version key order.",
            )
        if count > 1:
            raise GenerationError(
                f"manifest.json has multiple '{name}' entries; this should be "
                f"impossible. Inspect the file.",
            )

    return text


def validate_seed_coverage(
    seed: Seed,
    target_versions: dict[str, str],
) -> None:
    """Ensure every seeded tool is reflected in the manifest target set.

    Args:
        seed: Parsed seed mapping.
        target_versions: Result of ``build_target_versions``.

    Raises:
        GenerationError: If a seeded npm/pypi tool has no manifest entry to
            update.
    """
    expected = set()
    for owners in (seed.npm_owners, seed.pypi_owners):
        for tool in owners.values():
            if tool is not None:
                expected.add(_toolname_to_manifest_name(tool))
    missing = sorted(expected - set(target_versions))
    if missing:
        raise GenerationError(
            f"manifest.json has no entry for seeded tool(s): {missing}. "
            f"Add manifest entries before running the generator.",
        )


def _toolname_to_manifest_name(toolname_attr: str) -> str:
    """Convert ``ToolName`` attribute (uppercase) to its manifest string.

    ``ToolName`` is a ``StrEnum`` with ``auto()``; values match member names
    lowercased. ``ASTRO_CHECK`` -> ``astro_check``.

    Args:
        toolname_attr: Attribute name as written in source (e.g. ``OXFMT``).

    Returns:
        Manifest-form name (e.g. ``oxfmt``).
    """
    return toolname_attr.lower()


def collect_outputs(seed: Seed) -> tuple[str, str]:
    """Compute desired ``_generated_versions.py`` and ``manifest.json`` text.

    Args:
        seed: Parsed seed mapping.

    Returns:
        Tuple of (generated module text, manifest text).

    Raises:
        GenerationError: If any input is missing, malformed, or inconsistent.
    """
    pkg_versions = read_package_json(
        PACKAGE_JSON_PATH,
        strict_packages=set(seed.npm_owners),
    )

    npm_versions: dict[str, str] = {}
    for pkg in seed.npm_owners:
        if pkg not in pkg_versions:
            raise GenerationError(
                f"npm package '{pkg}' from seed not found in package.json",
            )
        npm_versions[pkg] = pkg_versions[pkg]

    pypi_versions = read_pyproject_versions(
        PYPROJECT_PATH,
        set(seed.pypi_owners),
    )

    binary_versions = read_binary_tool_versions(TOOL_VERSIONS_PATH)

    manifest_current = MANIFEST_PATH.read_text()
    manifest_data = json.loads(manifest_current)
    target_versions = build_target_versions(
        manifest_data=manifest_data,
        npm_versions=npm_versions,
        pypi_versions=pypi_versions,
        binary_versions=binary_versions,
    )
    validate_seed_coverage(seed, target_versions)

    generated_text = render_generated_module(npm_versions, pypi_versions)
    manifest_text = render_manifest(manifest_current, target_versions)
    return generated_text, manifest_text


def diff_text(label: str, current: str, desired: str) -> str:
    """Return a unified diff between current and desired text, or empty."""
    if current == desired:
        return ""
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        desired.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(diff)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Generate tool-version artifacts from canonical sources.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 with a diff if outputs would change; do not write.",
    )
    args = parser.parse_args(argv)

    try:
        seed = parse_seed(SEED_PATH)
        generated_text, manifest_text = collect_outputs(seed)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    current_generated = GENERATED_PATH.read_text() if GENERATED_PATH.exists() else ""
    current_manifest = MANIFEST_PATH.read_text()

    gen_diff = diff_text(
        str(GENERATED_PATH.relative_to(REPO_ROOT)),
        current_generated,
        generated_text,
    )
    manifest_diff = diff_text(
        str(MANIFEST_PATH.relative_to(REPO_ROOT)),
        current_manifest,
        manifest_text,
    )

    if args.check:
        if gen_diff or manifest_diff:
            sys.stdout.write(gen_diff)
            sys.stdout.write(manifest_diff)
            print(
                "\nDrift detected. Run scripts/ci/generate-tool-versions.py "
                "to regenerate.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        return EXIT_OK

    GENERATED_PATH.write_text(generated_text)
    MANIFEST_PATH.write_text(manifest_text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
