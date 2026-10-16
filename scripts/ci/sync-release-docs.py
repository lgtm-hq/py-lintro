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

    python scripts/ci/sync-release-docs.py [VERSION]
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PRE_COMMIT_DOC = Path("docs") / "pre-commit.md"

_PRE_COMMIT_REV_RE = re.compile(
    r"^(?P<prefix>\s*rev: )v\d+\.\d+\.\d+",
    re.MULTILINE,
)
# Same shape as ``update-security-support.py`` so a malformed NEXT_VERSION
# cannot stamp ``rev: vgarbage`` while SECURITY.md is rejected.
_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.\d+(?:[a-zA-Z0-9._-]+)?$")


def validate_version(version: str) -> str:
    """Return a stripped semver-like string or raise.

    Args:
        version: Candidate version, optionally with a leading ``v``.

    Returns:
        str: Semver without a leading ``v``.

    Raises:
        ValueError: If ``version`` is not a recognizable semver-like string.
    """
    stripped = version.strip().lstrip("v")
    if _VERSION_RE.match(stripped) is None:
        raise ValueError(f"Unrecognized version string: {version!r}")
    return stripped


def _read_pyproject_version(*, repo_root: Path | None = None) -> str:
    """Return the project version from ``pyproject.toml``.

    Args:
        repo_root: Repository root; defaults to the script's parent tree.

    Returns:
        str: Semver string without a leading ``v``.

    Raises:
        RuntimeError: If the version field cannot be parsed.
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    pyproject = root / "pyproject.toml"
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        version = str(data["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        msg = f"Could not parse version from {pyproject}"
        raise RuntimeError(msg) from exc
    return validate_version(version)


def resolve_version(
    *,
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> str:
    """Resolve the release version from argv, ``NEXT_VERSION``, or pyproject.

    ``validate_version`` and ``_read_pyproject_version`` raise
    ``ValueError`` / ``RuntimeError`` for invalid or unreadable versions.

    Args:
        argv: Command-line arguments excluding the program name.
        env: Environment mapping; defaults to ``os.environ``.
        repo_root: Repository root used for the pyproject fallback.

    Returns:
        str: Semver without a leading ``v``.
    """
    if argv:
        return validate_version(argv[0])
    mapping = env if env is not None else os.environ
    raw = mapping.get("NEXT_VERSION", "").strip()
    if raw:
        return validate_version(raw)
    return _read_pyproject_version(repo_root=repo_root)


def update_pre_commit_rev_pins(
    text: str,
    *,
    version: str,
) -> str:
    """Replace ``rev: vX.Y.Z`` examples in pre-commit documentation.

    Args:
        text: Full markdown document.
        version: Semver without a leading ``v``.

    Returns:
        str: Document with updated ``rev:`` pins.

    Raises:
        RuntimeError: If the document contains no ``rev: vX.Y.Z`` pins.
    """
    tag = f"v{version.lstrip('v')}"
    updated, count = _PRE_COMMIT_REV_RE.subn(rf"\g<prefix>{tag}", text)
    if count == 0:
        msg = "docs/pre-commit.md has no 'rev: vX.Y.Z' pins to sync"
        raise RuntimeError(msg)
    return updated


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

    Raises:
        RuntimeError: If ``docs/pre-commit.md`` is missing or has no rev pins.
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    path = root / _PRE_COMMIT_DOC
    if not path.is_file():
        msg = f"Missing required doc: {path}"
        raise RuntimeError(msg)
    original = path.read_text(encoding="utf-8")
    updated = update_pre_commit_rev_pins(original, version=version)
    if updated == original:
        print(f"{path.relative_to(root)} already synced to {version}.")
        return []
    path.write_text(updated, encoding="utf-8")
    print(f"Synced {path.relative_to(root)} to release {version}.")
    return [path]


def main(argv: list[str]) -> int:
    """Sync release docs in place.

    Args:
        argv: Command-line arguments excluding the program name. A leading
            version argument, when present, wins over ``NEXT_VERSION``.

    Returns:
        int: ``2`` on an invalid version string, ``1`` on a missing or
            pin-less target doc, otherwise ``0``.
    """
    try:
        version = resolve_version(argv=argv)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1
    try:
        sync_release_docs(version=version)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
