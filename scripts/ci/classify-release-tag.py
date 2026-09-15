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

A tag is a **release candidate** iff it is exactly ``X.Y.ZrcN`` (PEP 440, the
form the checkpoint procedure in ``.github/workflows/README.md`` produces).
Only that form can opt into the validation channels below.

Two validation-only switches (#2633) are read here, and only here, so that no
other job in the tag pipeline reads ``vars.*``:

* ``RELEASE_VALIDATION_CHANNELS`` — ``validation_channels=true`` iff the tag
  is a release candidate AND the variable is exactly ``true``. The pipeline
  then publishes npm under dist-tag ``next`` and promotes the Docker images
  under ``<version>`` only; Homebrew stays skipped. Any other tag, or any
  other value, yields ``false`` and every existing prerelease gate is
  unchanged.
* ``RELEASE_FAULT`` — passed through (trimmed) as ``release_fault``. The
  ``fail-build`` and ``fail-publish-npm`` steps compare against it and exit 1
  only on an exact match; unset is the no-op default.

Usage:
    python3 scripts/ci/classify-release-tag.py <tag>

Behavior:
    - Prints ``is_prerelease=…``, ``is_rc=…``, ``validation_channels=…`` and
      ``release_fault=…`` to stdout, one per line.
    - When ``GITHUB_OUTPUT`` is set, appends the same lines so GitHub Actions
      jobs can gate on ``steps.<id>.outputs.<name>``.

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

# Release candidate: the PEP 440 bare ``rcN`` form only. SemVer ``-rc.N`` is
# a prerelease but not a candidate for the validation channels: the build
# preflight requires the tag to equal the pyproject version, which is PEP 440.
_RC_TAG = re.compile(r"^v?\d+\.\d+\.\d+rc\d+$")

#: Repository variable that opens the validation channels for ``rcN`` tags.
VALIDATION_CHANNELS_ENV = "RELEASE_VALIDATION_CHANNELS"
#: Repository variable naming the fault to inject (``fail-build`` or
#: ``fail-publish-npm``); unset or empty injects nothing.
RELEASE_FAULT_ENV = "RELEASE_FAULT"
#: The fault names the pipeline's fault steps recognise.
KNOWN_FAULTS = ("fail-build", "fail-publish-npm")


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


def is_rc_tag(*, tag: str) -> bool:
    """Return whether ``tag`` is a PEP 440 release candidate (``X.Y.ZrcN``).

    Args:
        tag: The git tag name, with or without a leading ``v``.

    Returns:
        ``True`` only for the bare ``rcN`` form; alpha, beta, SemVer ``-rc.N``,
        stable and unrecognized tags all return ``False``.
    """
    return bool(_RC_TAG.match(tag.strip()))


def validation_channels_enabled(*, tag: str, switch: str | None) -> bool:
    """Return whether the validation channels open for this tag.

    Args:
        tag: The git tag name.
        switch: The raw ``RELEASE_VALIDATION_CHANNELS`` value (``None`` when
            the variable is unset).

    Returns:
        ``True`` iff ``tag`` is a release candidate and ``switch`` is exactly
        ``true`` after trimming. A stable tag never opens them, whatever the
        variable says, so the switch cannot change a real release.
    """
    return is_rc_tag(tag=tag) and (switch or "").strip() == "true"


def release_fault(*, fault: str | None) -> str:
    """Return the trimmed fault name to inject, or the empty string.

    Args:
        fault: The raw ``RELEASE_FAULT`` value (``None`` when unset).

    Returns:
        The trimmed value. Unknown names pass through unchanged: no fault
        step matches them, so they inject nothing, and the run log shows
        exactly what the variable held.
    """
    return (fault or "").strip()


def classify(*, tag: str, environ: dict[str, str] | os._Environ[str]) -> dict[str, str]:
    """Build the full output map for ``tag`` under the given environment.

    Args:
        tag: The git tag name.
        environ: The environment to read the two switches from.

    Returns:
        Output name to string value, in the order they are emitted.
    """
    enabled = validation_channels_enabled(
        tag=tag,
        switch=environ.get(VALIDATION_CHANNELS_ENV),
    )
    return {
        "is_prerelease": "true" if is_prerelease_tag(tag=tag) else "false",
        "is_rc": "true" if is_rc_tag(tag=tag) else "false",
        "validation_channels": "true" if enabled else "false",
        "release_fault": release_fault(fault=environ.get(RELEASE_FAULT_ENV)),
    }


def _write_output(*, outputs: dict[str, str]) -> None:
    """Emit the classification to stdout and to ``GITHUB_OUTPUT`` when set."""
    lines = [f"{name}={value}" for name, value in outputs.items()]
    for line in lines:
        print(line)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in lines))


def main() -> int:
    """Classify the tag passed as the sole positional argument."""
    if len(sys.argv) != 2:
        print(
            f"Usage: {Path(sys.argv[0]).name} <tag>",
            file=sys.stderr,
        )
        return 1

    _write_output(outputs=classify(tag=sys.argv[1], environ=os.environ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
