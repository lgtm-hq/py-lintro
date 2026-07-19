#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Stamp the ``SECURITY.md`` supported-versions table for the release version.

The release Version-PR generator (lgtm-ci ``reusable-release-version-pr``) only
rewrites ``CHANGELOG.md``, ``pyproject.toml``, ``uv.lock`` and
``lintro/__init__.py``. The ``SECURITY.md`` supported-versions table therefore
goes stale on every minor/major bump, and
``tests/test_documentation.py::test_security_md_supports_current_minor`` — which
requires the table to list the current ``major.minor.x`` line — fails the
unattended release PR (#1372). Patch bumps never hit this because the
``major.minor`` line is unchanged.

This module rewrites the two support rows of every ``SECURITY.md`` in the
repository (root and ``.github/`` if both exist) to the current release line:

* the ``major.minor.x`` row → the current ``major.minor``;
* the ``< major.minor`` row → the current ``major.minor``.

The rewrite is idempotent: on a patch bump the table already carries the current
line, so the file is left byte-for-byte unchanged (a no-op). Only the version
text inside each row's first cell is rewritten; the support marks
(``✅``/``❌`` in the root file, ``:white_check_mark:``/``:x:`` in
``.github/SECURITY.md``) and the surrounding column alignment are preserved, so
the output stays ``prettier``/``markdownlint`` compliant without a reflow pass.

It is wired into ``.github/workflows/release-version-pr.yml`` via the reusable
workflow's ``version-update-script`` orchestrator, which runs after the version
is stamped and before the version PR is committed. That job has no Node
toolchain and blocks npm egress, so only the standard library is used here.

Run standalone to stamp the repository ``SECURITY.md`` files in place::

    python scripts/ci/update-security-support.py [VERSION]

``VERSION`` defaults to the ``RELEASE_VERSION`` environment variable, then to
``[project].version`` in ``pyproject.toml`` (the source the release generator
has already stamped).
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

# Heading that introduces the supported-versions table in every SECURITY.md.
_SECTION_HEADING_RE = re.compile(r"^\s{0,3}##\s+Supported Versions\s*$", re.I)
_NEXT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")

# First-cell content of the two rewritable rows, after trimming cell padding.
_SUPPORTED_ROW_RE = re.compile(r"^\d+\.\d+\.x$")
_UNSUPPORTED_ROW_RE = re.compile(r"^<\s*\d+\.\d+$")

# A markdown separator row (``| --- | --- |``) that must never be treated as a
# data row.
_SEPARATOR_ROW_RE = re.compile(r"^[\s:|-]+$")

_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.\d+(?:[a-zA-Z0-9._-]+)?$")


def parse_major_minor(version: str) -> tuple[int, int]:
    """Extract the ``major`` and ``minor`` components of a version string.

    Args:
        version: A semver-like version string (e.g. ``"0.81.0"``). Pre-release
            or build metadata suffixes on the patch component are tolerated.

    Returns:
        tuple[int, int]: The ``(major, minor)`` integer pair.

    Raises:
        ValueError: If ``version`` is not a recognizable semver-like string.
    """
    match = _VERSION_RE.match(version.strip())
    if match is None:
        raise ValueError(f"Unrecognized version string: {version!r}")
    return int(match.group("major")), int(match.group("minor"))


def _rewrite_cell(cell: str, new_content: str) -> str:
    """Rewrite a table cell's content while preserving its column width.

    A ``prettier``-formatted table cell is one leading space, the content,
    right-padding spaces, then one trailing space. The rewritten content is
    re-padded to the original cell's inner field width so pipe positions — and
    therefore the whole table's alignment — are preserved whenever the column
    width is unchanged (the common case, since the ``Version`` header keeps the
    column at least seven columns wide).

    Args:
        cell: The raw cell text between two pipes, including its padding.
        new_content: The new trimmed cell content.

    Returns:
        str: The rewritten cell text, re-padded to the original inner width.
    """
    inner_width = max(len(cell) - 2, len(new_content))
    return " " + new_content.ljust(inner_width) + " "


def _rewrite_row(row: str, major: int, minor: int) -> str:
    """Rewrite a single support-table row to the current ``major.minor`` line.

    Rows whose first cell is neither a ``major.minor.x`` line nor a
    ``< major.minor`` line are returned unchanged.

    Args:
        row: A single markdown table row line.
        major: Current release major version.
        minor: Current release minor version.

    Returns:
        str: The row, with its version cell rewritten when applicable.
    """
    if _SEPARATOR_ROW_RE.match(row):
        return row
    stripped = row.strip()
    if not stripped.startswith("|"):
        return row
    # Split into cells, keeping the leading/trailing empty fields produced by
    # the surrounding pipes so the row can be rejoined verbatim.
    cells = row.split("|")
    if len(cells) < 3:
        return row
    first = cells[1]
    content = first.strip()
    if _SUPPORTED_ROW_RE.match(content):
        cells[1] = _rewrite_cell(first, f"{major}.{minor}.x")
    elif _UNSUPPORTED_ROW_RE.match(content):
        cells[1] = _rewrite_cell(first, f"< {major}.{minor}")
    else:
        return row
    return "|".join(cells)


def update_security_support(text: str, major: int, minor: int) -> str:
    """Rewrite the supported-versions table to the current ``major.minor`` line.

    Only rows inside the ``## Supported Versions`` section are considered, and
    only the two known row shapes (``major.minor.x`` and ``< major.minor``) are
    rewritten. All other content — including the support marks and the section
    heading — is preserved. The transformation is idempotent.

    Args:
        text: The full ``SECURITY.md`` document.
        major: Current release major version.
        minor: Current release minor version.

    Returns:
        str: The document with the support table stamped to ``major.minor``.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_section = False
    for line in lines:
        if _SECTION_HEADING_RE.match(line):
            in_section = True
            out.append(line)
            continue
        if in_section and _NEXT_HEADING_RE.match(line):
            in_section = False
            out.append(line)
            continue
        if in_section:
            out.append(_rewrite_row(line, major=major, minor=minor))
            continue
        out.append(line)
    return "\n".join(out)


def _security_files(repo_root: Path) -> list[Path]:
    """Return the ``SECURITY.md`` files present in the repository.

    Args:
        repo_root: Repository root directory.

    Returns:
        list[Path]: Existing ``SECURITY.md`` paths (root then ``.github/``).
    """
    candidates = [repo_root / "SECURITY.md", repo_root / ".github" / "SECURITY.md"]
    return [path for path in candidates if path.is_file()]


def _resolve_version(argv: list[str], repo_root: Path) -> str:
    """Resolve the release version from argv, env, then ``pyproject.toml``.

    Args:
        argv: Command-line arguments excluding the program name.
        repo_root: Repository root directory.

    Returns:
        str: The resolved version string.
    """
    if argv:
        return argv[0].strip()
    env_version = os.environ.get("RELEASE_VERSION", "").strip()
    if env_version:
        return env_version
    with (repo_root / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def main(argv: list[str]) -> int:
    """Stamp every repository ``SECURITY.md`` support table in place.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        int: Process exit code. Returns ``2`` on an invalid version string;
        otherwise ``0`` (a repository with no ``SECURITY.md`` is a non-fatal
        skip so the release Version-PR job is never blocked).
    """
    repo_root = Path(__file__).resolve().parents[2]
    version = _resolve_version(argv, repo_root)
    try:
        major, minor = parse_major_minor(version)
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2

    files = _security_files(repo_root)
    if not files:
        print("::warning::no SECURITY.md found, skipping support-table update")
        return 0

    for path in files:
        original = path.read_text(encoding="utf-8")
        updated = update_security_support(original, major=major, minor=minor)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"Stamped {path} support table to {major}.{minor}.x.")
        else:
            print(f"{path} support table already at {major}.{minor}.x.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
