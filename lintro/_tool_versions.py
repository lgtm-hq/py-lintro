"""Tool version requirements for lintro.

This module loads external tool versions from a manifest when available, with
fallbacks to package.json and legacy in-module constants.

External tools are those that users must install separately (not bundled with
lintro). Bundled Python tools (ruff, black, bandit, etc.) are managed via
pyproject.toml dependencies but can be pinned in the manifest for deterministic
Docker builds.

Version sources (in priority order):
- Manifest (lintro/tools/manifest.json) — authoritative when present
- npm tools (prettier, oxlint, etc.): Read from package.json
  (Renovate updates it natively)
- Non-npm tools (hadolint, shellcheck, etc.): Defined in TOOL_VERSIONS below
  (Renovate updates via custom regex managers)

IMPORTANT: manifest.json and TOOL_VERSIONS must stay in sync for
binary/cargo/rustup tools. CI enforces this via verify-manifest-sync.py.
When adding or updating a tool version, update BOTH files.

For shell scripts that need these versions, use:
    python3 -c "from lintro._tool_versions import get_tool_version; \
print(get_tool_version('toolname'))"
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.enums.tool_name import ToolName, normalize_tool_name

# Use stdlib logging to avoid external dependencies (this module must be
# importable in Docker build context before lintro dependencies are installed)
_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

# Manifest path (preferred source of truth for tool versions)
_MANIFEST_PATH = Path(__file__).parent / "tools" / "manifest.json"

# Non-npm external tools - updated by Renovate via custom regex managers
# Keys use ToolName enum values for type safety
TOOL_VERSIONS: dict[ToolName | str, str] = {
    ToolName.ACTIONLINT: "1.7.12",
    ToolName.CARGO_AUDIT: "0.22.0",
    ToolName.CARGO_DENY: "0.19.0",
    ToolName.CLIPPY: "1.94.0",
    ToolName.GITLEAKS: "8.30.1",
    ToolName.HADOLINT: "2.14.0",
    ToolName.OSV_SCANNER: "2.3.8",
    ToolName.PYTEST: "9.0.3",
    ToolName.RUSTC: "1.94.0",
    ToolName.RUSTFMT: "1.8.0",
    ToolName.SEMGREP: "1.151.0",
    ToolName.SHELLCHECK: "0.11.0",
    ToolName.SHFMT: "3.13.0",
    ToolName.SQLFLUFF: "4.0.0",
    ToolName.TAPLO: "0.10.0",
}

# Mapping from npm package names to ToolName for npm-managed tools
# These versions are read from package.json at runtime
_NPM_PACKAGE_TO_TOOL: dict[str, ToolName] = {
    "astro": ToolName.ASTRO_CHECK,
    "svelte-check": ToolName.SVELTE_CHECK,
    "typescript": ToolName.TSC,
    "vue-tsc": ToolName.VUE_TSC,
    "prettier": ToolName.PRETTIER,
    "markdownlint-cli2": ToolName.MARKDOWNLINT,
    "oxlint": ToolName.OXLINT,
    "oxfmt": ToolName.OXFMT,
}

# Reverse mapping for lookups
_TOOL_TO_NPM_PACKAGE: dict[ToolName, str] = {
    v: k for k, v in _NPM_PACKAGE_TO_TOOL.items()
}


# Fallback npm tool versions - used when package.json is not found.
# CI should verify these match package.json to prevent drift.
_FALLBACK_NPM_VERSIONS: dict[ToolName, str] = {
    ToolName.ASTRO_CHECK: "6.1.10",
    ToolName.SVELTE_CHECK: "4.4.6",
    ToolName.TSC: "5.9.3",
    ToolName.VUE_TSC: "3.2.6",
    ToolName.PRETTIER: "3.8.1",
    ToolName.MARKDOWNLINT: "0.22.0",
    ToolName.OXLINT: "1.58.0",
    ToolName.OXFMT: "0.43.0",
}

# Companion npm packages - packages that support a tool but have separate versions
# These are NOT mapped to ToolName (they're companion packages, not tools themselves)
# Used by install scripts to get the correct version for related packages
_COMPANION_NPM_PACKAGES: dict[str, str] = {
    "@astrojs/check": "0.9.8",  # Type checking companion for astro
}


@lru_cache(maxsize=1)
def _load_manifest_versions() -> tuple[dict[ToolName, str], dict[str, ToolName]]:
    """Load tool versions from the manifest, if present.

    Returns:
        Tuple of:
        - versions: mapping of ToolName -> version string
        - npm_map: mapping of npm package name -> ToolName
    """
    if not _MANIFEST_PATH.exists():
        return {}, {}

    try:
        data = json.loads(_MANIFEST_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        _logger.debug("Failed to read manifest: %s", exc)
        return {}, {}

    tools = data.get("tools", [])
    if not isinstance(tools, list):
        return {}, {}

    versions: dict[ToolName, str] = {}
    npm_map: dict[str, ToolName] = {}
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        version = entry.get("version")
        if not name or not version:
            continue
        try:
            tool_name = normalize_tool_name(str(name))
        except ValueError:
            continue
        versions[tool_name] = str(version)
        install = entry.get("install", {})
        if isinstance(install, dict) and install.get("type") == "npm":
            package = install.get("package")
            if package:
                npm_map[str(package)] = tool_name

    return versions, npm_map


@lru_cache(maxsize=1)
def _load_package_json() -> dict[str, str]:
    """Load all npm package versions from package.json.

    Tries multiple paths to find package.json. Returns all dependencies
    with versions stripped of ^ or ~ prefixes.

    Returns:
        Dictionary mapping npm package names to version strings.
    """
    possible_paths = [
        # Development: py-lintro/package.json
        Path(__file__).parent.parent / "package.json",
        # If bundled alongside module
        Path(__file__).parent / "package.json",
    ]

    for package_json_path in possible_paths:
        if package_json_path.exists():
            try:
                data = json.loads(package_json_path.read_text())
                dev_deps = data.get("devDependencies", {})
                deps = data.get("dependencies", {})
                all_deps = {**deps, **dev_deps}

                # Strip ^ or ~ prefix from all versions
                return {pkg: ver.lstrip("^~") for pkg, ver in all_deps.items()}
            except (json.JSONDecodeError, OSError):
                continue

    return {}


@lru_cache(maxsize=1)
def _load_npm_versions() -> dict[ToolName, str]:
    """Load npm tool versions from package.json with fallback.

    Tries multiple paths to find package.json, falling back to hardcoded
    versions if not found. This handles both development environments and
    pip-installed packages.

    This is cached to avoid repeated file reads.

    Returns:
        Dictionary mapping ToolName to version string for npm-managed tools.
    """
    all_deps = _load_package_json()

    if all_deps:
        # Start with fallback versions, then overlay package.json values
        versions: dict[ToolName, str] = dict(_FALLBACK_NPM_VERSIONS)
        for npm_pkg, tool_name in _NPM_PACKAGE_TO_TOOL.items():
            if npm_pkg in all_deps:
                versions[tool_name] = all_deps[npm_pkg]

        _logger.debug("Loaded npm versions (package.json + fallbacks)")
        return versions

    # Fallback: use hardcoded minimum versions when package.json not found
    _logger.debug("package.json not found, using fallback npm versions")
    return dict(_FALLBACK_NPM_VERSIONS)


def get_tool_version(tool_name: ToolName | str) -> str | None:
    """Get the expected version for an external tool.

    Args:
        tool_name: Name of the tool (ToolName enum or string).
            Also accepts npm package names like "typescript" for "tsc",
            or companion npm packages like "@astrojs/check".

    Returns:
        Version string if found, None otherwise.
    """
    manifest_versions, manifest_npm_map = _load_manifest_versions()

    # Store original string for fallback npm package lookup
    original_name = tool_name if isinstance(tool_name, str) else None

    # Convert string to ToolName if it's an npm package alias
    if isinstance(tool_name, str):
        if tool_name in manifest_npm_map:
            tool_name = manifest_npm_map[tool_name]
        elif tool_name in _NPM_PACKAGE_TO_TOOL:
            tool_name = _NPM_PACKAGE_TO_TOOL[tool_name]
        else:
            try:
                tool_name = normalize_tool_name(tool_name)
            except ValueError:
                # Not a known tool - try looking up as raw npm package
                if original_name:
                    return _get_npm_package_version(original_name)
                return None

    # Check manifest first (single source of truth when available)
    if tool_name in manifest_versions:
        return manifest_versions[tool_name]

    # Check npm-managed tools first
    npm_versions = _load_npm_versions()
    if tool_name in npm_versions:
        return npm_versions[tool_name]

    # Check non-npm tools
    return TOOL_VERSIONS.get(tool_name)


def _get_npm_package_version(package_name: str) -> str | None:
    """Get version for a raw npm package from package.json.

    This is used for companion packages (like @astrojs/check) that are needed
    for installation but aren't mapped to a ToolName.

    Args:
        package_name: The npm package name (e.g., "@astrojs/check").

    Returns:
        Version string if found in package.json or fallback, None otherwise.
    """
    # Try package.json first
    all_deps = _load_package_json()
    if package_name in all_deps:
        return all_deps[package_name]

    # Fallback to companion packages dict
    return _COMPANION_NPM_PACKAGES.get(package_name)


def get_min_version(tool_name: ToolName) -> str:
    """Get the minimum required version for an external tool.

    Use this in tool definitions for the min_version field. Unlike get_tool_version,
    this raises an error if the tool isn't registered, ensuring all external tools
    are tracked.

    Args:
        tool_name: ToolName enum member.

    Returns:
        Version string.

    Raises:
        KeyError: If tool_name is not found in either TOOL_VERSIONS or package.json.
    """
    version = get_tool_version(tool_name)
    if version is None:
        raise KeyError(
            f"Tool '{tool_name}' not found. "
            f"Add it to TOOL_VERSIONS in lintro/_tool_versions.py "
            f"or package.json (for npm tools).",
        )
    return version


def get_all_expected_versions() -> dict[ToolName | str, str]:
    """Get all expected external tool versions.

    Returns:
        Dictionary mapping tool names to version strings.
        Includes both npm-managed and non-npm tools.
    """
    # Start with non-npm tools (fallback)
    all_versions: dict[ToolName | str, str] = dict(TOOL_VERSIONS)

    # Add npm-managed tools (fallback)
    npm_versions = _load_npm_versions()
    for tool_name, version in npm_versions.items():
        all_versions[tool_name] = version

    # Load manifest versions and override fallbacks (manifest is authoritative)
    manifest_versions, _ = _load_manifest_versions()
    if manifest_versions:
        for k, v in manifest_versions.items():
            all_versions[k] = v

    return all_versions


def is_npm_managed(tool_name: ToolName) -> bool:
    """Check if a tool's version is managed via npm/package.json.

    Args:
        tool_name: ToolName enum member.

    Returns:
        True if the tool version comes from package.json, False otherwise.
    """
    _, manifest_npm_map = _load_manifest_versions()
    if manifest_npm_map:
        return tool_name in manifest_npm_map.values()
    return tool_name in _TOOL_TO_NPM_PACKAGE
