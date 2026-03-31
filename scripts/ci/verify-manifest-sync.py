#!/usr/bin/env python3
"""Verify manifest tool versions align with pyproject.toml and package.json."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

# Cache for TOOL_VERSIONS loaded from _tool_versions.py
_tool_versions_cache: dict[str, str] | None = None


def _load_tool_versions(repo_root: Path) -> dict[str, str]:
    """Load TOOL_VERSIONS from _tool_versions.py by regex extraction.

    Parses the TOOL_VERSIONS dict from source to avoid importing lintro
    (which may have uninstalled dependencies in CI).

    Args:
        repo_root: Path to the repository root.

    Returns:
        Dictionary mapping tool names (underscore-based) to version strings.
    """
    global _tool_versions_cache  # noqa: PLW0603
    if _tool_versions_cache is not None:
        return _tool_versions_cache

    tv_path = repo_root / "lintro" / "_tool_versions.py"
    content = tv_path.read_text()

    # Extract entries like: ToolName.ACTIONLINT: "1.7.10",
    pattern = re.compile(r'ToolName\.(\w+)\s*:\s*"([^"]+)"')
    versions: dict[str, str] = {}
    for match in pattern.finditer(content):
        tool_name = match.group(1).lower()
        version = match.group(2)
        versions[tool_name] = version

    _tool_versions_cache = versions
    return versions


def _parse_requirement_safe(req_str: str) -> Requirement | None:
    """Parse a requirement string safely, returning None on failure.

    Args:
        req_str: PEP 508 requirement string (e.g., "ruff>=0.4.0,<1.0").

    Returns:
        Parsed Requirement object, or None if parsing fails.
    """
    # Strip environment markers before parsing
    req_str = req_str.split(";", 1)[0].strip()
    if not req_str:
        return None
    try:
        return Requirement(req_str)
    except InvalidRequirement:
        return None


def _parse_version_safe(version_str: str) -> Version | None:
    """Parse a version string safely, returning None on failure.

    Args:
        version_str: PEP 440 version string (e.g., "1.2.3", "1.0a1").

    Returns:
        Parsed Version object, or None if parsing fails.
    """
    try:
        return Version(version_str)
    except InvalidVersion:
        return None


def _load_pyproject_deps(path: Path) -> dict[str, list[Requirement]]:
    """Load dependencies from pyproject.toml.

    Args:
        path: Path to pyproject.toml file.

    Returns:
        Dictionary mapping normalized package names to list of Requirement objects.
    """
    data = tomllib.loads(path.read_text())
    deps: dict[str, list[Requirement]] = {}

    def add_req(req_str: str) -> None:
        req = _parse_requirement_safe(req_str)
        if req:
            # Normalize package name for lookup
            normalized_name = req.name.lower().replace("-", "_").replace(".", "_")
            deps.setdefault(normalized_name, []).append(req)

    for req in data.get("project", {}).get("dependencies", []) or []:
        add_req(req)
    for group in (
        data.get("project", {}).get("optional-dependencies", {}) or {}
    ).values():
        for req in group:
            add_req(req)
    for group in (data.get("dependency-groups", {}) or {}).values():
        for req in group:
            add_req(req)

    return deps


def _load_package_json_versions(path: Path) -> dict[str, str]:
    """Load package versions from package.json."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    deps = {
        **data.get("dependencies", {}),
        **data.get("devDependencies", {}),
    }
    normalized: dict[str, str] = {}
    for name, version in deps.items():
        normalized[name] = _normalize_npm_version(str(version))
    return normalized


def _normalize_npm_version(version: str) -> str:
    """Normalize npm version string to extract semver."""
    version = version.strip()
    lowered = version.lower()
    if lowered in {"*", "x"}:
        return version
    if any(
        lowered.startswith(prefix)
        for prefix in (
            "file:",
            "git+",
            "github:",
            "http:",
            "https:",
            "ssh:",
            "workspace:",
            "link:",
        )
    ):
        return version
    if re.search(r"(?i)(^|[^\w])\d+\.(x|\*)([^\w]|$)", lowered):
        return version
    if re.search(r"(?i)(^|[^\w])\d+\.\d+\.(x|\*)([^\w]|$)", lowered):
        return version

    match = re.search(
        r"\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?",
        version,
    )
    if match:
        return match.group(0)
    return version


def _iter_manifest_tools(path: Path) -> Iterable[dict[str, Any]]:
    """Iterate over tools in manifest.json."""
    data = json.loads(path.read_text())
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("manifest tools must be a list")
    return [tool for tool in tools if isinstance(tool, dict)]


def _normalize_package_name(name: str) -> str:
    """Normalize package name for comparison (PEP 503)."""
    return name.lower().replace("-", "_").replace(".", "_")


def main() -> int:
    """Verify manifest.json versions match pyproject.toml and package.json."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-missing-versions",
        action="store_true",
        default=os.environ.get("LINTRO_STRICT_MISSING_VERSIONS", "").lower()
        in {"1", "true", "yes"},
        help="Treat dependencies without version specs as errors.",
    )
    args = parser.parse_args()

    strict_missing_versions = args.strict_missing_versions
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = repo_root / "lintro" / "tools" / "manifest.json"
    pyproject_path = repo_root / "pyproject.toml"
    package_json_path = repo_root / "package.json"

    errors: list[str] = []
    warnings: list[str] = []

    py_deps = _load_pyproject_deps(pyproject_path)
    package_versions = _load_package_json_versions(package_json_path)

    for tool in _iter_manifest_tools(manifest_path):
        name = str(tool.get("name", "")).strip()
        version_str = str(tool.get("version", "")).strip()
        install = tool.get("install", {})
        install_type = install.get("type") if isinstance(install, dict) else None

        if not name or not version_str:
            errors.append(f"Manifest entry missing name/version: {tool}")
            continue

        # Parse the manifest version
        manifest_version = _parse_version_safe(version_str)
        if not manifest_version:
            errors.append(f"{name}: invalid manifest version '{version_str}'")
            continue

        if install_type == "npm":
            package_name = install.get("package") if isinstance(install, dict) else None
            if not package_name:
                errors.append(f"{name}: npm entry missing install.package")
                continue
            pkg_version = package_versions.get(str(package_name))
            if not pkg_version:
                errors.append(
                    f"{name}: npm package '{package_name}' not in package.json",
                )
                continue
            if pkg_version != version_str:
                errors.append(
                    f"{name}: manifest {version_str} != "
                    f"package.json {package_name} {pkg_version}",
                )
            continue

        if install_type == "pip":
            package_spec = install.get("package") if isinstance(install, dict) else None
            package_spec = package_spec or name

            # Parse install.package to check for embedded version constraints
            install_req = _parse_requirement_safe(str(package_spec))
            if (
                install_req
                and install_req.specifier
                and not install_req.specifier.contains(
                    manifest_version,
                    prereleases=True,
                )
            ):
                errors.append(
                    f"{name}: manifest {version_str} "
                    f"doesn't satisfy install.package {package_spec}",
                )

            # Look up package in pyproject.toml dependencies
            if install_req:
                pkg_name = install_req.name
            else:
                # Fallback: strip common version specifier characters
                pkg_name = re.split(r"[<>=!~\[\];]", str(package_spec))[0].strip()
            normalized_pkg_name = _normalize_package_name(pkg_name)
            entries = py_deps.get(normalized_pkg_name, [])
            if not entries:
                errors.append(f"{name}: missing from pyproject.toml dependencies")
                continue

            for req in entries:
                if not req.specifier:
                    message = f"{name}: dependency '{req}' has no version spec"
                    if strict_missing_versions:
                        errors.append(message)
                    else:
                        warnings.append(message)
                    continue

                # Check if manifest version satisfies the specifier
                # This handles all operators: ==, >=, <=, ~=, !=, >, <, and compounds
                if not req.specifier.contains(manifest_version, prereleases=True):
                    errors.append(
                        f"{name}: manifest {version_str} "
                        f"doesn't satisfy pyproject {req}",
                    )
            continue

        # Binary, cargo, and rustup tools: verify against TOOL_VERSIONS
        if install_type in {"binary", "cargo", "rustup"}:
            tool_versions = _load_tool_versions(repo_root)
            tv_version = tool_versions.get(name)
            if tv_version is None:
                errors.append(
                    f"{name}: missing from TOOL_VERSIONS in _tool_versions.py",
                )
            elif tv_version != version_str:
                errors.append(
                    f"{name}: manifest {version_str} != TOOL_VERSIONS {tv_version}",
                )
            continue

    if errors:
        print("Manifest sync check failed:")
        for error in errors:
            print(f"  - {error}")
    if warnings:
        print("Manifest sync warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        return 1

    print(
        "Manifest versions are aligned with "
        "pyproject.toml, package.json, and TOOL_VERSIONS",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
