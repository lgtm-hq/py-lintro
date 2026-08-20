#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Sync release-sensitive documentation to the current version.

``docs/pre-commit.md`` shows ``rev:`` pins in its copy-paste examples. Those
pins go stale on every release and turn the recommended snippet into outdated
advice (#1319), so this finalizer rewrites them to the version being released.

Supported-version tables in ``SECURITY.md`` and ``.github/SECURITY.md`` are
deliberately *not* handled here — :mod:`update_security_support`
(``scripts/ci/update-security-support.py``) already owns them and preserves the
per-file support marks and column alignment.

Intended to run from the release Version-PR finalizer
(``scripts/ci/finalize-version-pr.py``), where ``NEXT_VERSION`` is set to the
semver being released. That job has no Node toolchain and blocks npm egress, so
only the standard library is used here.

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
    changed: list[Path] = []
    for path in (root / "docs" / "pre-commit.md",):
        if not path.is_file():
            print(f"::warning::Skipping missing doc: {path}")
            continue
        original = path.read_text(encoding="utf-8")
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
