#!/usr/bin/env python3
"""Diff tool names / versions between an old and a new manifest.

Used by ``compute-new-manifest-tools.sh`` to derive allowlists for the
manifest-vs-image gate:

* ``--emit added`` (default): tool names present in the new manifest but
  absent from the old one → fed to ``--allow-missing`` so a PR-introduced
  tool's absent binary in the digest-pinned base image is tolerated (#1565).
* ``--emit version-changed``: tool names whose declared version changed
  between the old and new manifest → fed to ``--allow-version-lag`` so a
  PR that bumps a baked tool can merge before the tools image republishes
  (#1582).

JSON parsing lives here; git resolution (merge-base, ``git show``) lives in
the shell wrapper.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _tool_names(manifest_path: Path) -> set[str]:
    """Extract the set of tool names declared in a manifest file.

    A path that does not exist yields an empty set: the "old" side of the diff
    is absent when the manifest is brand new, in which case every tool in the
    new manifest counts as added.

    Args:
        manifest_path: Path to a manifest JSON file.

    Returns:
        The set of non-empty ``tools[].name`` values.

    Raises:
        ValueError: When the manifest is not a JSON object or ``tools`` is not
            a list of objects.
    """
    if not manifest_path.exists():
        return set()
    data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object, got {type(data).__name__}")
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("manifest tools must be a list")
    names: set[str] = set()
    for entry in tools:
        if not isinstance(entry, dict):
            raise ValueError("manifest tools[] entries must be objects")
        name = str(entry.get("name", "")).strip()
        if name:
            names.add(name)
    return names


def _tool_versions(manifest_path: Path) -> dict[str, str]:
    """Extract ``name -> version`` mappings declared in a manifest file.

    A path that does not exist yields an empty dict (same "brand-new
    manifest" semantics as :func:`_tool_names`).

    Args:
        manifest_path: Path to a manifest JSON file.

    Returns:
        Mapping of non-empty tool names to their declared version strings.
        Entries missing a non-empty version are omitted.

    Raises:
        ValueError: When the manifest is not a JSON object or ``tools`` is not
            a list of objects.
    """
    if not manifest_path.exists():
        return {}
    data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object, got {type(data).__name__}")
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("manifest tools must be a list")
    versions: dict[str, str] = {}
    for entry in tools:
        if not isinstance(entry, dict):
            raise ValueError("manifest tools[] entries must be objects")
        name = str(entry.get("name", "")).strip()
        version = str(entry.get("version", "")).strip()
        if name and version:
            versions[name] = version
    return versions


def _version_changed_names(
    old_versions: dict[str, str],
    new_versions: dict[str, str],
) -> list[str]:
    """Return sorted names whose version string changed between manifests.

    Only tools present in *both* manifests are considered. Newly-added tools
    are handled by ``--emit added`` / ``--allow-missing`` instead.

    Args:
        old_versions: Name → version from the base-branch manifest.
        new_versions: Name → version from the current manifest.

    Returns:
        Sorted list of tool names whose version string differs.
    """
    changed: list[str] = []
    for name, new_version in new_versions.items():
        old_version = old_versions.get(name)
        if old_version is not None and old_version != new_version:
            changed.append(name)
    return sorted(changed)


def main() -> int:
    """Print comma-separated tool names for the requested emit mode.

    Returns:
        ``0`` on success (the name list, possibly empty, is printed to
        stdout); ``2`` when either manifest cannot be parsed. Callers treat a
        non-zero exit as "fail closed": an empty allowlist, full enforcement.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Print tool names added or version-changed between two manifests."
        ),
    )
    parser.add_argument(
        "--old",
        required=True,
        help="Path to the base-branch manifest (may not exist -> empty set)",
    )
    parser.add_argument(
        "--new",
        required=True,
        help="Path to the current manifest",
    )
    parser.add_argument(
        "--emit",
        choices=("added", "version-changed"),
        default="added",
        help=(
            "What to print: tools newly added to the manifest (default), or "
            "tools whose declared version changed between the two manifests."
        ),
    )
    args = parser.parse_args()

    try:
        if args.emit == "version-changed":
            old_versions = _tool_versions(Path(args.old))
            new_versions = _tool_versions(Path(args.new))
            names = _version_changed_names(old_versions, new_versions)
        else:
            old_names = _tool_names(Path(args.old))
            new_names = _tool_names(Path(args.new))
            names = sorted(new_names - old_names)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Failed to compute new manifest tools: {exc}", file=sys.stderr)
        return 2

    print(",".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
