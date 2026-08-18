"""Read canonical version sources: package.json, pyproject.toml, _tool_versions.py."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from _generator.errors import GenerationError
from _generator.seed import extract_assign_target

# Specifier pattern for ``pkg[extras]>=version`` style PEP 508 strings.
_SPEC_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)"
    r"(?:\[[^\]]+\])?"
    r"\s*(?:(?P<op>>=|==|~=|!=|>|<=|<)\s*(?P<version>[0-9][^,;\s]*))?"
    r"(?:\s*[,;].*)?$",
)

# Strict exact-version pattern for npm specs after ``^``/``~`` stripping.
# Accepts ``X.Y.Z`` plus optional prerelease and build metadata SemVer suffixes.
_SEMVER_IDENTIFIER = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_EXACT_VERSION_RE = re.compile(
    rf"^\d+\.\d+\.\d+(?:-{_SEMVER_IDENTIFIER})?(?:\+{_SEMVER_IDENTIFIER})?$",
)


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


def read_pyproject_versions(
    path: Path,
    packages: set[str],
    repo_root: Path | None = None,
) -> dict[str, str]:
    """Read exact versions for the given pypi packages from ``pyproject.toml``.

    Scans the known dependency tables (see ``_collect_dep_strings``). When
    the same package appears in multiple tables, all occurrences must
    declare the same version or generation fails.

    Args:
        path: Path to ``pyproject.toml``.
        packages: Set of package names to extract.
        repo_root: Used to format error paths relative to the repo, if given.

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
                f"in {_display_path(path, repo_root)}",
            )
        if len(observed) > 1:
            raise GenerationError(
                f"pypi package '{pkg}' has inconsistent versions across "
                f"pyproject.toml tables: {sorted(observed)}. "
                f"Pin one canonical version.",
            )
        versions[pkg] = next(iter(observed))

    return versions


def read_requirements_pin(
    path: Path,
    package: str,
    repo_root: Path | None = None,
) -> str:
    """Read an exact ``package==version`` pin from a requirements.txt file.

    Ignores comments, extras, and environment markers. The package must appear
    exactly once with an ``==`` pin.

    Args:
        path: Path to the requirements file.
        package: Package name to extract (case-insensitive PEP 503).
        repo_root: Used to format error paths relative to the repo, if given.

    Returns:
        The pinned version string.

    Raises:
        GenerationError: If the file is missing, the package is absent, the
            pin is not exact, or the package appears more than once.
    """
    if not path.exists():
        raise GenerationError(
            f"requirements file not found: {_display_path(path, repo_root)}",
        )

    needle = _normalize_package_name(package)
    found: list[str] = []
    for spec in _requirement_specs(path.read_text()):
        match = _SPEC_RE.match(spec)
        if match is None:
            continue
        if _normalize_package_name(match.group("name")) != needle:
            continue
        op = match.group("op")
        version = match.group("version")
        if op != "==" or not version:
            raise GenerationError(
                f"package '{package}' in {_display_path(path, repo_root)} "
                f"must be pinned with == (got {spec!r})",
            )
        found.append(version)

    if not found:
        raise GenerationError(
            f"package '{package}' not found with an == pin in "
            f"{_display_path(path, repo_root)}",
        )
    if len(found) > 1:
        raise GenerationError(
            f"package '{package}' has multiple pins in "
            f"{_display_path(path, repo_root)}: {found}",
        )
    return found[0]


def _requirement_specs(text: str) -> list[str]:
    """Yield ``name==version`` specs, dropping comments, hashes, and wraps.

    ``uv pip compile --generate-hashes`` emits backslash-continued lines
    and ``--hash=`` flags. Those must not hide the package pin.

    Args:
        text: Raw requirements file contents.

    Returns:
        PEP 508 requirement strings with pip CLI flags removed.
    """
    specs: list[str] = []
    buf = ""
    for raw_line in text.splitlines():
        without_comment = raw_line.split("#", 1)[0].rstrip()
        if without_comment.endswith("\\"):
            buf += without_comment[:-1] + " "
            continue
        line = f"{buf}{without_comment}".strip()
        buf = ""
        spec = _strip_pip_flags(line)
        if spec:
            specs.append(spec)
    trailing = _strip_pip_flags(buf.strip())
    if trailing:
        specs.append(trailing)
    return specs


def _strip_pip_flags(line: str) -> str:
    """Remove ``--hash`` / other pip CLI flags from a requirement line.

    Args:
        line: A (possibly joined) requirement line.

    Returns:
        The requirement specifier, or empty if the line was flags-only.
    """
    if not line:
        return ""
    tokens = [token for token in line.split() if not token.startswith("--")]
    return " ".join(tokens)


def _normalize_package_name(name: str) -> str:
    """PEP 503 package-name normalization.

    Args:
        name: Raw package name.

    Returns:
        Normalized package name.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _display_path(path: Path, repo_root: Path | None) -> str:
    """Format a path relative to ``repo_root`` if possible, else absolute.

    Args:
        path: Path to display.
        repo_root: Optional repository root for relative formatting.

    Returns:
        Relative path string when possible, otherwise the original path.
    """
    if repo_root is None:
        return str(path)
    try:
        return str(path.relative_to(repo_root))
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
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise GenerationError(f"_tool_versions.py is not valid Python: {exc}") from exc
    block: ast.Assign | ast.AnnAssign | None = None
    for node in ast.walk(tree):
        if extract_assign_target(node) == "TOOL_VERSIONS":
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
