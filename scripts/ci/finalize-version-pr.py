#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Finalize the auto-generated release Version-PR before it is committed.

The release Version-PR generator (lgtm-ci ``reusable-release-version-pr``)
accepts a single repo-side ``version-update-script`` that runs after the version
is stamped and the ``CHANGELOG.md`` is written, but before the version PR is
committed. Before finalizing, reject a candidate whose tag already exists or
whose version no longer advances live main (#3054). This read-only guard runs
again after the publish gate, closing the stale-trigger window before the hook.
The shared reusable still needs a final check at PR creation (see
``docs/release-race-recovery.md``). Then run the repo-side finalizers in order:

1. Reflow ``CHANGELOG.md`` to lintro's 88-column markdown budget so the release
   PR passes dogfooding-lint (#1117), via
   :func:`format_changelog.format_changelog`.
2. Stamp the root ``SECURITY.md`` supported-versions table to the current
   release ``major.minor`` line so minor/major bumps do not go stale and break
   the unattended release PR (#1372), via
   :func:`update_security_support.main`.
3. Point the pinned py-lintro release image at the newest published release so
   the digest-lag gate cannot drift (#1590), via
   :func:`sync_pinned_release_image.main`. Deliberately last, and non-fatal:
   it reports problems as warnings and never blocks a release.

The finalizer scripts have hyphenated filenames, so they are loaded by path
rather than imported as packages. That job has no Node toolchain and blocks npm
egress, so only the standard library is used here.

Run standalone to finalize the repository in place::

    NEXT_VERSION=X.Y.Z python scripts/ci/finalize-version-pr.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess  # nosec B404 - fixed read-only git commands, shell=False
import tomllib
from pathlib import Path
from types import ModuleType

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _git_read(*, repo_root: Path, args: list[str]) -> str:
    """Read Git state without changing the checkout or remote.

    Args:
        repo_root: Repository working directory.
        args: Fixed Git subcommand and arguments.

    Returns:
        The command's standard output.
    """
    return subprocess.run(  # nosec B603 B607 - fixed git reads, shell=False
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout


def _stable_version(version: str) -> tuple[int, ...]:
    """Parse the stable version used by the version-PR generator.

    Args:
        version: Candidate or main's project version.

    Returns:
        Numeric components for version ordering.

    Raises:
        ValueError: If the version is not a stable X.Y.Z version.
    """
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError(f"Expected a stable X.Y.Z release version, got {version!r}")
    return tuple(int(part) for part in version.split("."))


def check_release_candidate(*, repo_root: Path, version: str) -> None:
    """Reject an already consumed version before finalizing a release PR.

    Consult live origin, rather than the triggering checkout's stale refs.
    The hook has no token: this public repository needs only read access.
    If main advanced beyond the objects fetched at checkout, fail closed and
    let a fresh run reevaluate; never fetch, move tags, or repair publication.
    Git read failures propagate to the hook's failing exit status.

    Args:
        repo_root: Repository containing the generated version files.
        version: NEXT_VERSION supplied by the reusable.

    Raises:
        ValueError: If the candidate is invalid, tagged or does not advance main.
    """
    candidate = _stable_version(version)
    main_ref = "refs/heads/main"
    tag_ref = f"refs/tags/v{version}"
    refs = {
        ref: sha
        for sha, ref in (
            line.split()
            for line in _git_read(
                repo_root=repo_root,
                args=["ls-remote", "origin", main_ref, tag_ref],
            ).splitlines()
        )
    }
    if tag_ref in refs:
        raise ValueError(
            f"Tag v{version} already exists; refuse a duplicate release PR",
        )
    main_sha = refs.get(main_ref, "")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", main_sha):
        raise ValueError("Cannot resolve live origin/main; refuse a release PR")
    base = tomllib.loads(
        _git_read(repo_root=repo_root, args=["show", f"{main_sha}:pyproject.toml"]),
    )
    current = str(base["project"]["version"])
    # Checkpoint prereleases on main may graduate to the same stable version.
    current_match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)\d+)?", current)
    if current_match is None:
        raise ValueError(f"Cannot compare live main version {current!r}")
    current_release = _stable_version(current_match[1])
    if candidate < current_release or (
        candidate == current_release and current_match[2] is None
    ):
        raise ValueError(
            f"Candidate {version} does not advance live main {current}; "
            "refuse a stale release PR",
        )


def _load(module_name: str, filename: str) -> ModuleType:
    """Load a hyphenated sibling script as an importable module.

    Args:
        module_name: Name to register the loaded module under.
        filename: Sibling script filename (e.g. ``"format-changelog.py"``).

    Returns:
        ModuleType: The loaded module.

    Raises:
        ImportError: If the module spec cannot be created or executed.
    """
    path = _SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Run every repo-side finalizer for the release Version-PR.

    Returns:
        int: ``1`` for an unsafe candidate, otherwise the first non-zero
        finalizer exit code or ``0``.
    """
    try:
        check_release_candidate(
            repo_root=_SCRIPTS_DIR.parents[1],
            version=os.environ.get("NEXT_VERSION", ""),
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"::error title=Release candidate rejected::{exc}")
        return 1

    changelog = _load("format_changelog", "format-changelog.py")
    security = _load("update_security_support", "update-security-support.py")
    pin_sync = _load("sync_pinned_release_image", "sync-pinned-release-image.py")

    result = int(changelog.main([]))
    if result != 0:
        return result
    result = int(security.main([]))
    if result != 0:
        return result
    # Always 0 by design — the pin sync warns instead of failing, so a
    # registry hiccup or a missing token cannot block an unattended release.
    return int(pin_sync.main([]))


if __name__ == "__main__":
    raise SystemExit(main())
