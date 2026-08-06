#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Sync release-sensitive documentation to the current version.

Updates supported-version tables in ``SECURITY.md`` and ``.github/SECURITY.md``,
and ``rev:`` pins in ``docs/pre-commit.md``. Intended to run from the lgtm-ci
``version-update-script`` hook (via :mod:`scripts.ci.version_update`) where
``NEXT_VERSION`` is set to the semver being released.

Run standalone to sync docs to ``pyproject.toml``'s version::

    python scripts/ci/sync-release-docs.py
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

_SUPPORTED_ROW_RE = re.compile(
    r"^(?P<prefix>\| )\d+\.\d+\.x(?P<suffix>  \| .*)$",
    re.MULTILINE,
)
_UNSUPPORTED_ROW_RE = re.compile(
    r"^(?P<prefix>\| < )\d+\.\d+(?P<suffix>  \| .*)$",
    re.MULTILINE,
)
_PRE_COMMIT_REV_RE = re.compile(
    r"^(?P<prefix>\s*rev: )v\d+\.\d+\.\d+",
    re.MULTILINE,
)


def _read_pyproject_version() -> str:
    """Return the project version from ``pyproject.toml``.

    Returns:
        str: Semver string without a leading ``v``.

    Raises:
        RuntimeError: If the version field cannot be parsed.
    """
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(?P<version>[^"]+)"', text, re.MULTILINE)
    if match is None:
        msg = f"Could not parse version from {_PYPROJECT}"
        raise RuntimeError(msg)
    return match.group("version")


def resolve_version(*, env: Mapping[str, str] | None = None) -> str:
    """Resolve the release version from ``NEXT_VERSION`` or ``pyproject.toml``.

    Args:
        env: Environment mapping; defaults to ``os.environ``.

    Returns:
        str: Semver without a leading ``v``.

    Raises:
        RuntimeError: If the version field cannot be parsed from ``pyproject.toml``.
    """
    mapping = env if env is not None else os.environ
    raw = mapping.get("NEXT_VERSION", "").strip()
    if raw:
        return raw.lstrip("v")
    try:
        return _read_pyproject_version()
    except RuntimeError as exc:
        msg = "No version found in NEXT_VERSION or pyproject.toml"
        raise RuntimeError(msg) from exc


def supported_release_line(*, major: int, minor: int) -> str:
    """Build the supported-version table cell (``major.minor.x``).

    Args:
        major: Semver major component.
        minor: Semver minor component.

    Returns:
        str: Supported release line label.
    """
    return f"{major}.{minor}.x"


def update_supported_version_table(text: str, *, major: int, minor: int) -> str:
    """Replace supported-version rows in a SECURITY policy table.

    Args:
        text: Full markdown document.
        major: Semver major component.
        minor: Semver minor component.

    Returns:
        str: Document with updated supported-version rows.
    """
    line = supported_release_line(major=major, minor=minor)
    threshold = f"{major}.{minor}"
    updated = _SUPPORTED_ROW_RE.sub(
        rf"\g<prefix>{line}\g<suffix>",
        text,
        count=1,
    )
    return _UNSUPPORTED_ROW_RE.sub(
        rf"\g<prefix>{threshold}\g<suffix>",
        updated,
        count=1,
    )


def update_pre_commit_rev_pins(text: str, *, version: str) -> str:
    """Replace ``rev: vX.Y.Z`` examples in pre-commit documentation.

    Args:
        text: Full markdown document.
        version: Semver without a leading ``v``.

    Returns:
        str: Document with updated ``rev:`` pins.
    """
    tag = f"v{version.lstrip('v')}"
    return _PRE_COMMIT_REV_RE.sub(rf"\g<prefix>{tag}", text)


def sync_release_docs(
    *,
    version: str,
    repo_root: Path | None = None,
) -> list[Path]:
    """Update release-sensitive docs for ``version``.

    Args:
        version: Semver without a leading ``v``.
        repo_root: Repository root; defaults to the script's parent tree.

    Returns:
        list[Path]: Paths that were rewritten.
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    major_str, minor_str, _patch_str = version.lstrip("v").split(".", maxsplit=2)
    major = int(major_str)
    minor = int(minor_str)

    targets: list[tuple[Path, str]] = [
        (root / "SECURITY.md", "security"),
        (root / ".github" / "SECURITY.md", "security"),
        (root / "docs" / "pre-commit.md", "pre-commit"),
    ]
    changed: list[Path] = []
    for path, kind in targets:
        if not path.is_file():
            print(f"::warning::Skipping missing doc: {path}")
            continue
        original = path.read_text(encoding="utf-8")
        if kind == "security":
            updated = update_supported_version_table(
                original,
                major=major,
                minor=minor,
            )
        else:
            updated = update_pre_commit_rev_pins(original, version=version)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(path)
            print(f"Synced {path.relative_to(root)} to release {version}.")
        else:
            print(f"{path.relative_to(root)} already synced to {version}.")
    return changed


def main(argv: list[str]) -> int:  # noqa: ARG001
    """Sync release docs in place.

    Args:
        argv: Command-line arguments (unused).

    Returns:
        int: Process exit code.
    """
    try:
        version = resolve_version()
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1
    sync_release_docs(version=version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
