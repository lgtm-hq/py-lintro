r"""Tool version requirements for lintro.

Version sources (in priority order):
    1. ``lintro/tools/manifest.json`` — authoritative when present.
    2. ``lintro/_generated_versions.py`` — npm and pypi tool versions,
       written by ``scripts/ci/generate-tool-versions.py`` from the canonical
       ``package.json`` and ``pyproject.toml`` sources.
    3. ``TOOL_VERSIONS`` below — non-npm/non-pypi tools (binaries, cargo,
       rustup) updated by Renovate via custom regex managers.

Single-source-of-truth structure:

    package.json / pyproject.toml   <- canonical (Renovate writes here)
              |
              v
    scripts/ci/generate-tool-versions.py
              |
              v
    lintro/_generated_versions.py   <- generated, committed, ships in wheel
              |
              v
    lintro/_tool_versions.py        <- this module (re-exports + helpers)

The generator is the only writer of ``_generated_versions.py``. Runtime
consumers read from it directly; there is no overlay onto ``package.json`` /
``pyproject.toml`` at runtime, so a stale generated module fails CI's
``--check`` gate the same way it would fail locally.

Adding a new tool:
    - npm or pypi: add a ToolName, edit ``lintro/_tool_packages.py``, pin in
      package.json or pyproject.toml, run the generator.
    - Other (binary/cargo/rustup): add to ``TOOL_VERSIONS`` below and add a
      Renovate ``customManager`` entry.

For shell scripts:
    python3 -c "from lintro._tool_versions import get_tool_version; \\
print(get_tool_version('toolname'))"
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from lintro._generated_versions import NPM_VERSIONS, PYPI_VERSIONS
from lintro._tool_packages import NPM_PACKAGE_OWNERS, PYPI_PACKAGE_OWNERS
from lintro.enums.tool_name import ToolName, normalize_tool_name

# Use stdlib logging to avoid external dependencies (this module must be
# importable in Docker build context before lintro dependencies are installed)
_logger = logging.getLogger(__name__)

# Manifest path (preferred source of truth for tool versions)
_MANIFEST_PATH = Path(__file__).parent / "tools" / "manifest.json"

# Non-npm/non-pypi external tools — updated by Renovate via custom regex
# managers. Tools managed via npm or pypi live in ``_tool_packages.py``
# (seeds) and ``_generated_versions.py`` (versions).
TOOL_VERSIONS: dict[ToolName | str, str] = {
    ToolName.ACTIONLINT: "1.7.12",
    ToolName.CARGO_AUDIT: "0.22.0",
    ToolName.CARGO_DENY: "0.19.0",
    ToolName.CLIPPY: "1.94.0",
    ToolName.GITLEAKS: "8.30.1",
    ToolName.HADOLINT: "2.14.0",
    ToolName.OSV_SCANNER: "2.3.8",
    ToolName.RUSTC: "1.94.0",
    ToolName.RUSTFMT: "1.8.0",
    ToolName.SHELLCHECK: "0.11.0",
    ToolName.SHFMT: "3.13.0",
    ToolName.TAPLO: "0.10.0",
}

_NPM_PACKAGE_TO_TOOL: dict[str, ToolName] = {
    pkg: tool for pkg, tool in NPM_PACKAGE_OWNERS.items() if tool is not None
}

_PYPI_PACKAGE_TO_TOOL: dict[str, ToolName] = {
    pkg: tool for pkg, tool in PYPI_PACKAGE_OWNERS.items() if tool is not None
}

_TOOL_TO_NPM_PACKAGE: dict[ToolName, str] = {
    v: k for k, v in _NPM_PACKAGE_TO_TOOL.items()
}

_NPM_VERSIONS_BY_TOOL: dict[ToolName, str] = {
    tool: NPM_VERSIONS[pkg] for pkg, tool in _NPM_PACKAGE_TO_TOOL.items()
}

_PYPI_VERSIONS_BY_TOOL: dict[ToolName, str] = {
    tool: PYPI_VERSIONS[pkg] for pkg, tool in _PYPI_PACKAGE_TO_TOOL.items()
}

_COMPANION_NPM_PACKAGES: dict[str, str] = {
    pkg: NPM_VERSIONS[pkg] for pkg, tool in NPM_PACKAGE_OWNERS.items() if tool is None
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

    # Store original string for companion npm-package lookup
    original_name = tool_name if isinstance(tool_name, str) else None

    # Convert string to ToolName if it's a known alias
    if isinstance(tool_name, str):
        if tool_name in manifest_npm_map:
            tool_name = manifest_npm_map[tool_name]
        elif tool_name in _NPM_PACKAGE_TO_TOOL:
            tool_name = _NPM_PACKAGE_TO_TOOL[tool_name]
        elif tool_name in _PYPI_PACKAGE_TO_TOOL:
            tool_name = _PYPI_PACKAGE_TO_TOOL[tool_name]
        else:
            try:
                tool_name = normalize_tool_name(tool_name)
            except ValueError:
                # Not a known tool - try looking up as a companion npm package
                if original_name:
                    return _get_npm_package_version(original_name)
                return None

    # Manifest is authoritative when present
    if tool_name in manifest_versions:
        return manifest_versions[tool_name]

    # npm- and pypi-managed tools (generated from package.json/pyproject.toml)
    if tool_name in _NPM_VERSIONS_BY_TOOL:
        return _NPM_VERSIONS_BY_TOOL[tool_name]
    if tool_name in _PYPI_VERSIONS_BY_TOOL:
        return _PYPI_VERSIONS_BY_TOOL[tool_name]

    # Other tools (binaries, cargo, rustup)
    return TOOL_VERSIONS.get(tool_name)


def _get_npm_package_version(package_name: str) -> str | None:
    """Get the version for a raw npm package by name.

    Used for companion packages (e.g. ``@astrojs/check``) that are needed for
    installation but aren't mapped to a ToolName.

    Args:
        package_name: The npm package name (e.g. ``@astrojs/check``).

    Returns:
        Version string if the package is a known companion, else None.
    """
    return _COMPANION_NPM_PACKAGES.get(package_name)


def get_min_version(tool_name: ToolName) -> str:
    """Get the minimum required version for an external tool.

    Use this in tool definitions for the ``min_version`` field. Unlike
    ``get_tool_version``, this raises if the tool isn't registered.

    Args:
        tool_name: ToolName enum member.

    Returns:
        Version string.

    Raises:
        KeyError: If the tool is not registered.
    """
    version = get_tool_version(tool_name)
    if version is None:
        raise KeyError(
            f"Tool '{tool_name}' not found. "
            f"Add it to TOOL_VERSIONS, or to lintro/_tool_packages.py with a "
            f"matching pin in package.json/pyproject.toml.",
        )
    return version


def get_all_expected_versions() -> dict[ToolName | str, str]:
    """Get all expected external tool versions.

    Combines, in priority order, ``TOOL_VERSIONS`` (binary/cargo/rustup),
    generated npm/pypi tool versions, and manifest overrides.

    Returns:
        Dictionary mapping tool names to version strings.
    """
    all_versions: dict[ToolName | str, str] = dict(TOOL_VERSIONS)
    for tool_name, version in _NPM_VERSIONS_BY_TOOL.items():
        all_versions[tool_name] = version
    for tool_name, version in _PYPI_VERSIONS_BY_TOOL.items():
        all_versions[tool_name] = version

    manifest_versions, _ = _load_manifest_versions()
    for tool_name, version in manifest_versions.items():
        all_versions[tool_name] = version

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
