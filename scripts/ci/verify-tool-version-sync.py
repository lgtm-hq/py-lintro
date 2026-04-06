#!/usr/bin/env python3
"""Verify tool versions are correctly loaded from their sources.

This script validates that:
1. npm tool versions are correctly read from package.json
2. Non-npm tool versions are defined in _tool_versions.py
3. All expected tools have versions available
4. Fallback npm versions match package.json to prevent drift
5. Dockerfile ARG versions are consistent across all Dockerfiles

Exit codes:
    0: All versions are correctly loaded
    1: Missing versions or error occurred
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> int:
    """Verify tool versions are correctly loaded."""
    # Find project root (script is in scripts/ci/)
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent.parent

    print(f"Checking version loading in: {project_root}")
    print()

    # Add project to path for imports
    sys.path.insert(0, str(project_root))

    from lintro._tool_versions import (
        _FALLBACK_NPM_VERSIONS,
        _NPM_PACKAGE_TO_TOOL,
        TOOL_VERSIONS,
        get_tool_version,
        is_npm_managed,
    )
    from lintro.enums.tool_name import ToolName

    errors: list[str] = []

    # Load package.json for fallback validation
    package_json_path = project_root / "package.json"
    package_json_versions: dict[str, str] = {}
    if package_json_path.exists():
        try:
            pkg_data = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Failed to read package.json: {exc}")
        else:
            all_deps = {
                **pkg_data.get("dependencies", {}),
                **pkg_data.get("devDependencies", {}),
            }
            package_json_versions = {k: v.lstrip("^~") for k, v in all_deps.items()}

    # Check that all npm-managed tools have versions from package.json
    print("npm-managed tools (from package.json):")
    for npm_pkg, tool_name in _NPM_PACKAGE_TO_TOOL.items():
        version = get_tool_version(tool_name)
        if version:
            print(f"  ✓ {tool_name.value} ({npm_pkg}): {version}")
        else:
            errors.append(f"npm tool '{tool_name.value}' ({npm_pkg}) has no version")
            print(f"  ✗ {tool_name.value} ({npm_pkg}): MISSING")

    print()

    # Validate fallback npm versions match package.json
    print("Fallback npm version sync check:")
    for fallback_tool, fallback_version in _FALLBACK_NPM_VERSIONS.items():
        pkg_name: str | None = next(
            (pkg for pkg, t in _NPM_PACKAGE_TO_TOOL.items() if t == fallback_tool),
            None,
        )
        if pkg_name is not None and pkg_name in package_json_versions:
            pkg_version = package_json_versions[pkg_name]
            if fallback_version == pkg_version:
                print(f"  ✓ {fallback_tool.value}: fallback matches package.json")
            else:
                errors.append(
                    f"Fallback version for {fallback_tool.value} ({fallback_version}) "
                    f"doesn't match package.json ({pkg_version})",
                )
                print(
                    f"  ✗ {fallback_tool.value}: fallback {fallback_version} != "
                    f"package.json {pkg_version}",
                )
        elif pkg_name:
            print(f"  ? {fallback_tool.value}: not in package.json (skipped)")

    print()

    # Check that all non-npm tools have versions
    print("Non-npm tools (from _tool_versions.py):")
    for tool_name_key, version in TOOL_VERSIONS.items():
        if isinstance(tool_name_key, ToolName):
            print(f"  ✓ {tool_name_key.value}: {version}")

    print()

    # Verify is_npm_managed returns correct values
    print("Verifying npm-managed detection:")
    for tool_name in _NPM_PACKAGE_TO_TOOL.values():
        if not is_npm_managed(tool_name):
            errors.append(f"is_npm_managed({tool_name}) should be True")
            print(f"  ✗ {tool_name.value} should be npm-managed")
        else:
            print(f"  ✓ {tool_name.value} correctly identified as npm-managed")

    for tool_name_key in TOOL_VERSIONS:
        if isinstance(tool_name_key, ToolName) and is_npm_managed(tool_name_key):
            errors.append(f"is_npm_managed({tool_name_key}) should be False")
            print(f"  ✗ {tool_name_key.value} should NOT be npm-managed")

    print()

    # Check Dockerfile ARG version consistency across all Dockerfiles
    print("Dockerfile ARG version consistency:")
    arg_pattern = re.compile(r"^ARG\s+(\w+_VERSION)=([0-9]+\.[0-9]+\.[0-9]+)")
    dockerfile_args: dict[str, dict[str, str]] = {}
    for dockerfile in sorted(project_root.glob("Dockerfile*")):
        if dockerfile.is_file():
            try:
                content = dockerfile.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"Failed to read {dockerfile.name}: {exc}")
                print(f"  ✗ {dockerfile.name}: read error ({exc})")
                continue
            for line in content.splitlines():
                match = arg_pattern.match(line)
                if match:
                    arg_name, arg_value = match.group(1), match.group(2)
                    dockerfile_args.setdefault(arg_name, {})[
                        dockerfile.name
                    ] = arg_value

    for arg_name, file_versions in sorted(dockerfile_args.items()):
        unique_versions = set(file_versions.values())
        if len(unique_versions) == 1:
            version = next(iter(unique_versions))
            files = ", ".join(sorted(file_versions))
            print(f"  ✓ {arg_name}: {version} ({files})")
        else:
            detail = ", ".join(f"{f}={v}" for f, v in sorted(file_versions.items()))
            errors.append(f"Dockerfile {arg_name} mismatch: {detail}")
            print(f"  ✗ {arg_name} mismatch: {detail}")

    print()

    if errors:
        print("✗ Errors found:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("✓ All tool versions are correctly loaded from their sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
