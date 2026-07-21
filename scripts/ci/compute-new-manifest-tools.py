#!/usr/bin/env python3
"""Print tool names present in a new manifest but absent from an old one.

Used by ``compute-new-manifest-tools.sh`` to derive the set of tools a PR
introduces, so the manifest-vs-image gate can tolerate their (necessarily)
missing binary in the digest-pinned base image. JSON parsing lives here; git
resolution (merge-base, ``git show``) lives in the shell wrapper.
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


def main() -> int:
    """Print comma-separated tool names added between the old and new manifest.

    Returns:
        ``0`` on success (the added-name list, possibly empty, is printed to
        stdout); ``2`` when either manifest cannot be parsed. Callers treat a
        non-zero exit as "fail closed": an empty allowlist, full enforcement.
    """
    parser = argparse.ArgumentParser(
        description="Print tool names added between two manifests.",
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
    args = parser.parse_args()

    try:
        old_names = _tool_names(Path(args.old))
        new_names = _tool_names(Path(args.new))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Failed to compute new manifest tools: {exc}", file=sys.stderr)
        return 2

    added = sorted(new_names - old_names)
    print(",".join(added))
    return 0


if __name__ == "__main__":
    sys.exit(main())
