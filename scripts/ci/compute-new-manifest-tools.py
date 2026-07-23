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


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dotted numeric version into a comparable integer tuple.

    Non-numeric trailing segments (pre-release tags) stop parsing, so
    ``7.1.0-rc.1`` compares as ``(7, 1, 0)``. Mirrors the helper in
    ``verify-manifest-tools.py`` so both sides order versions identically.

    Args:
        version: A dotted version string.

    Returns:
        Integer segments for lexicographic comparison. Empty when no leading
        digits are found.
    """
    parts: list[int] = []
    for segment in version.split("."):
        digits = ""
        for char in segment:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) != len(segment):
            # Segment had trailing non-digit content (e.g. "0-rc"): stop
            # entirely so a pre-release tag drops everything after it and
            # "7.1.0-rc.1" compares as (7, 1, 0), per the documented contract.
            break
    return tuple(parts)


def _is_upward_bump(old_version: str, new_version: str) -> bool:
    """Return True only when *new* is strictly newer than *old*.

    ``--allow-version-lag`` exists solely for upward bumps: the digest-pinned
    image still ships the pre-bump (older) build, so an image-older-than-
    manifest mismatch is expected until the tools image republishes. A
    downgrade must NOT enter the allowlist — the pinned image would then be
    *newer* than the manifest, which is a real drift the gate must hard-fail
    (fail closed). Unparseable versions on either side also fail closed.

    Args:
        old_version: Version declared in the base-branch manifest.
        new_version: Version declared in the current manifest.

    Returns:
        True when both versions parse and ``new > old``; False otherwise.
    """
    old_parts = _version_tuple(old_version)
    new_parts = _version_tuple(new_version)
    if not old_parts or not new_parts:
        return False
    width = max(len(old_parts), len(new_parts))
    old_padded = old_parts + (0,) * (width - len(old_parts))
    new_padded = new_parts + (0,) * (width - len(new_parts))
    return new_padded > old_padded


def _version_changed_names(
    old_versions: dict[str, str],
    new_versions: dict[str, str],
) -> list[str]:
    """Return sorted names whose version was bumped *upward* between manifests.

    Only tools present in *both* manifests are considered. Newly-added tools
    are handled by ``--emit added`` / ``--allow-missing`` instead. Downgrades
    and unparseable version changes are deliberately excluded so they fail
    closed: ``--allow-version-lag`` is meant for upward bumps only, and a
    downgrade leaves the pinned image *newer* than the manifest — a real drift
    the gate must still hard-fail.

    Args:
        old_versions: Name → version from the base-branch manifest.
        new_versions: Name → version from the current manifest.

    Returns:
        Sorted list of tool names whose version was bumped upward.
    """
    changed: list[str] = []
    for name, new_version in new_versions.items():
        old_version = old_versions.get(name)
        if old_version is not None and _is_upward_bump(old_version, new_version):
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
