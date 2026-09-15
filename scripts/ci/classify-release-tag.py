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
* ``RELEASE_FAULT`` — passed through (trimmed) as ``release_fault`` for a
  release candidate only; for any other tag the output is the empty string,
  so a leftover variable can never break a stable release. The
  ``fail-build`` and ``fail-publish-npm`` steps compare against it and exit 1
  only on an exact match; unset is the no-op default.

Both variables are validated against an exact allowlist after trimming
(``RELEASE_VALIDATION_CHANNELS``: empty, ``true``, ``false``;
``RELEASE_FAULT``: empty, ``fail-build``, ``fail-publish-npm``). Any other
value, including one carrying a newline or another control character, fails
the job before anything is written: the outputs are derived from repository
variables and land in ``GITHUB_OUTPUT``, so an unvalidated value could inject
or override another output. As defence in depth every output is written in
the delimiter form with a random delimiter.

Usage:
    python3 scripts/ci/classify-release-tag.py <tag>

Behavior:
    - Prints exactly one line, ``is_prerelease=true`` or
      ``is_prerelease=false``, to stdout: the contract
      ``scripts/ci/mirror/resolve-version.sh`` captures. The other outputs
      are summarised on stderr for the run log.
    - When ``GITHUB_OUTPUT`` is set, appends ``is_prerelease``, ``is_rc``,
      ``validation_channels`` and ``release_fault`` (delimiter form) so
      GitHub Actions jobs can gate on ``steps.<id>.outputs.<name>``.

Exit codes:
    0 — Classification printed.
    1 — Invalid arguments (wrong number of positional arguments).
    2 — A validation variable holds a value outside its allowlist; nothing
        is written.
"""

from __future__ import annotations

import os
import re
import secrets
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
#: Exact allowlists (after trimming) for the two variables. Anything else
#: fails closed: the values end up in GITHUB_OUTPUT.
ALLOWED_FAULTS = frozenset({"", *KNOWN_FAULTS})
ALLOWED_SWITCH_VALUES = frozenset({"", "true", "false"})


class InvalidVariableError(ValueError):
    """A validation variable holds a value outside its allowlist."""


def _validated(*, name: str, value: str | None, allowed: frozenset[str]) -> str:
    """Return ``value`` trimmed, or raise when it is not allowlisted.

    Args:
        name: The variable name, for the error message.
        value: The raw variable value (``None`` when unset).
        allowed: The exact, case-sensitive allowlist.

    Returns:
        The trimmed value.

    Raises:
        InvalidVariableError: When the trimmed value is not in ``allowed``.
    """
    trimmed = (value or "").strip()
    if trimmed not in allowed:
        choices = ", ".join(repr(item) for item in sorted(allowed))
        msg = f"{name} must be one of {choices}; got {trimmed!r}"
        raise InvalidVariableError(msg)
    return trimmed


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
        variable says, so the switch cannot change a real release. A value
        outside the allowlist (empty, ``true``, ``false``) is rejected by
        ``_validated`` with ``InvalidVariableError``.
    """
    value = _validated(
        name=VALIDATION_CHANNELS_ENV,
        value=switch,
        allowed=ALLOWED_SWITCH_VALUES,
    )
    return is_rc_tag(tag=tag) and value == "true"


def release_fault(*, tag: str, fault: str | None) -> str:
    """Return the fault name to inject for ``tag``, or the empty string.

    Args:
        tag: The git tag name.
        fault: The raw ``RELEASE_FAULT`` value (``None`` when unset).

    Returns:
        The trimmed fault name for a release candidate; the empty string for
        every other tag, so a leftover variable cannot touch a stable
        release (same rule as the validation channels). A value outside the
        allowlist (empty, ``fail-build``, ``fail-publish-npm``) is rejected
        by ``_validated`` with ``InvalidVariableError``.
    """
    value = _validated(name=RELEASE_FAULT_ENV, value=fault, allowed=ALLOWED_FAULTS)
    return value if is_rc_tag(tag=tag) else ""


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
        "release_fault": release_fault(
            tag=tag,
            fault=environ.get(RELEASE_FAULT_ENV),
        ),
    }


def _write_output(*, outputs: dict[str, str]) -> None:
    """Emit the classification to stdout and to ``GITHUB_OUTPUT`` when set.

    ``GITHUB_OUTPUT`` receives the delimiter form (``name<<EOF_<random>``)
    with a fresh random delimiter per value, so a value can never be read
    as a second ``name=value`` line.
    """
    print(f"is_prerelease={outputs['is_prerelease']}")
    extras = " ".join(
        f"{name}={value!r}"
        for name, value in outputs.items()
        if name != "is_prerelease"
    )
    print(f"classify-release-tag.py: {extras}", file=sys.stderr)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            for name, value in outputs.items():
                delimiter = f"EOF_{secrets.token_hex(16)}"
                handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    """Classify the tag passed as the sole positional argument."""
    if len(sys.argv) != 2:
        print(
            f"Usage: {Path(sys.argv[0]).name} <tag>",
            file=sys.stderr,
        )
        return 1

    try:
        outputs = classify(tag=sys.argv[1], environ=os.environ)
    except InvalidVariableError as exc:
        print(f"::error title=Invalid release validation variable::{exc}")
        print(f"classify-release-tag.py: {exc}", file=sys.stderr)
        return 2
    _write_output(outputs=outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
