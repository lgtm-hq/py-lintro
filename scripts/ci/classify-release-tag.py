#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Classify a release tag as a stable release or a prerelease.

Downstream publish jobs (Homebrew, npm, Docker) must only run for stable
releases. Substring checks such as ``contains(github.ref_name, 'b')`` are
unsafe because they match anywhere in the tag: a stable tag like
``v1.2.3+build.1`` contains a ``b`` (in ``build``) and is wrongly treated as a
prerelease. This classifier applies an anchored version check instead.

A tag is considered **stable** iff, after stripping an optional leading ``v``,
it is exactly a three-part version core ``X.Y.Z`` optionally followed by
SemVer ``+build`` metadata. Any other suffix — SemVer ``-<prerelease>`` (for
example ``-rc.1``) or a PEP 440 bare prerelease (``a1``/``b2``/``rc1``) — marks
the tag as a prerelease. Build metadata never marks a tag as a prerelease.
Unrecognized tags default to prerelease so publishing fails closed.

Usage:
    python3 scripts/ci/classify-release-tag.py <tag>

Behavior:
    - Prints ``is_prerelease=true`` or ``is_prerelease=false`` to stdout.
    - When ``GITHUB_OUTPUT`` is set, appends the same ``is_prerelease`` line so
      GitHub Actions jobs can gate on ``steps.<id>.outputs.is_prerelease``.

Exit codes:
    0 — Classification printed.
    1 — Invalid arguments (wrong number of positional arguments).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Stable release: optional leading ``v``, a three-part ``X.Y.Z`` core, and an
# optional SemVer ``+build`` metadata segment. Anything else is a prerelease.
_STABLE_TAG = re.compile(
    r"^v?\d+\.\d+\.\d+(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$",
)


def is_prerelease_tag(*, tag: str) -> bool:
    """Return whether ``tag`` names a prerelease rather than a stable release.

    Args:
        tag: The git tag name, with or without a leading ``v`` (for example
            ``v1.2.3``, ``v1.2.3-rc.1``, ``v1.2.3rc1``, ``v1.2.3+build.1``).

    Returns:
        ``False`` when the tag is a stable ``X.Y.Z`` release (optionally with
        ``+build`` metadata); ``True`` for any prerelease or unrecognized tag.
    """
    return not bool(_STABLE_TAG.match(tag.strip()))


def _write_output(*, is_prerelease: bool) -> None:
    """Emit the classification to stdout and to ``GITHUB_OUTPUT`` when set."""
    line = f"is_prerelease={'true' if is_prerelease else 'false'}"
    print(line)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def main() -> int:
    """Classify the tag passed as the sole positional argument."""
    if len(sys.argv) != 2:
        print(
            f"Usage: {Path(sys.argv[0]).name} <tag>",
            file=sys.stderr,
        )
        return 1

    _write_output(is_prerelease=is_prerelease_tag(tag=sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
