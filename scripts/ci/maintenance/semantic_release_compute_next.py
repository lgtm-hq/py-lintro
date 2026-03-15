#!/usr/bin/env python3
"""Compute the next semantic version for releases.

This script computes the next version based on Conventional Commits since the
last baseline (git tag v*, or the last "chore(release): prepare X.Y.Z" commit,
or the current version declared in pyproject.toml). It writes
"next_version=<semver>" to GITHUB_OUTPUT when available.

It honors the environment variable MAX_BUMP. When MAX_BUMP="minor", any major
increments are clamped down to a minor bump of the current major version.

Usage:
  uv run python scripts/ci/semantic_release_compute_next.py [--print-only]

"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from lintro.enums.git_command import GitCommand
from lintro.enums.git_ref import GitRef

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Allowed git arguments for security validation
ALLOWED_GIT_DESCRIBE_ARGS = {
    "--tags",
    "--abbrev=0",
    "--no-merges",
    "-n",
    "1",
    "-1",
    "--match",
    "v*",
    "--pretty=%s",
    "--pretty=%B",
    "--pretty=format:%h",
    "--pretty=format:%s",
    "--pretty=format:%B",
    "--grep=^chore(release): prepare ",
}


@dataclass
class ComputeResult:
    """Result of computing next version.

    Attributes:
        base_ref: Git ref used as baseline (tag or commit)
        base_version: Base semantic version string
        next_version: Computed next semantic version string or empty string
        has_breaking: Whether breaking commits were detected
        has_feat: Whether feature commits were detected
        has_fix_or_perf: Whether fix/perf commits were detected
    """

    base_ref: str = field(default="")
    base_version: str = field(default="")
    next_version: str = field(default="")
    has_breaking: bool = field(default=False)
    has_feat: bool = field(default=False)
    has_fix_or_perf: bool = field(default=False)


def _validate_git_args(arguments: list[str]) -> None:
    """Validate git CLI arguments against a strict allowlist.

    This mitigates Bandit B603 by ensuring no untrusted or unexpected
    parameters are passed to the subprocess. Only the exact argument shapes
    used by this module are accepted.

    Args:
        arguments: Git arguments, excluding the executable path.

    Raises:
        ValueError: If any argument is unexpected or unsafe.
    """
    if not arguments:
        raise ValueError("missing git arguments")

    unsafe_chars = set(";&|><`$\\\n\r")
    for arg in arguments:
        if any(ch in arg for ch in unsafe_chars):
            raise ValueError("unsafe characters in git arguments")

    cmd = arguments[0]
    rest = arguments[1:]

    def is_sha(value: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", value))

    def is_head_range(value: str) -> bool:
        if value == GitRef.HEAD:
            return True
        # vX.Y.Z..HEAD or <sha>..HEAD
        return bool(
            re.fullmatch(
                rf"({TAG_RE.pattern[1:-1]}|[0-9a-fA-F]{{7,40}})\.\.HEAD",
                value,
            ),
        )

    if cmd == GitCommand.DESCRIBE:
        # Expected: describe --tags --abbrev=0 --match v*
        for a in rest:
            if a not in ALLOWED_GIT_DESCRIBE_ARGS:
                raise ValueError("unexpected git describe argument")
        return

    if cmd == GitCommand.REV_PARSE:
        # Expected: rev-parse HEAD
        if rest != [GitRef.HEAD]:
            raise ValueError("unexpected git rev-parse arguments")
        return

    if cmd == GitCommand.LOG:
        # Accept forms used in this module only
        if not rest:
            raise ValueError("git log requires additional arguments")
        # Validate each argument
        for a in rest:
            if a in ALLOWED_GIT_DESCRIBE_ARGS:
                continue
            if a.startswith("--pretty=") and a in ALLOWED_GIT_DESCRIBE_ARGS:
                continue
            if is_head_range(a) or is_sha(a):
                continue
            raise ValueError("unexpected git log argument")
        return

    raise ValueError("unsupported git command")


def run_git(*args: str) -> str:
    """Run a git command and capture stdout.

    Args:
        *args: Git arguments (e.g., 'log', '--pretty=%s').

    Raises:
        RuntimeError: If git executable is not found in PATH.

    Returns:
        str: Standard output string with trailing whitespace stripped.
    """
    git_path = shutil.which("git")
    if not git_path:
        raise RuntimeError("git executable not found in PATH")
    # Validate arguments against allowlist before executing
    _validate_git_args([*args])
    result = (
        subprocess.run(  # nosec B603 — args validated by _validate_git_args allowlist
            [git_path, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    )
    return (result.stdout or "").strip()


def read_last_tag() -> str:
    """Read the most recent v*-prefixed tag.

    Returns:
        Latest tag matching the pattern ``vX.Y.Z``.
    """
    return run_git("describe", "--tags", "--abbrev=0", "--match", "v*")


def read_last_prepare_commit() -> tuple[str, str]:
    """Read the last release-prepare commit and extract its version.

    Returns:
        Tuple of (short_sha, prepared_version). Empty strings if missing.
    """
    sha = run_git(
        "log",
        "--grep=^chore(release): prepare ",
        "--pretty=format:%h",
        "-n",
        "1",
        "--no-merges",
    )
    if not sha:
        return "", ""
    subject = run_git("log", "-1", "--pretty=format:%s", sha)
    m = re.search(r"prepare (\d+\.\d+\.\d+)", subject)
    return sha, (m.group(1) if m else "")


def read_pyproject_version() -> str:
    """Read the current version from ``pyproject.toml`` if present.

    Returns:
        Version string or an empty string when not found.
    """
    path = Path("pyproject.toml")
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        m = re.match(r"^version\s*=\s*\"(\d+\.\d+\.\d+)\"", line.strip())
        if m:
            return m.group(1)
    return ""


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semantic version into integer components.

    Args:
        version: Version string in the form ``MAJOR.MINOR.PATCH``.

    Returns:
        Tuple of (major, minor, patch); zeros when parsing fails.
    """
    m = SEMVER_RE.match(version)
    if not m:
        return 0, 0, 0
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def detect_commit_types(base_ref: str) -> tuple[bool, bool, bool]:
    """Detect breaking/feature/fix commits since a base reference.

    Args:
        base_ref: Baseline ref (tag or commit) to compare against.

    Returns:
        Tuple of booleans: (has_breaking, has_feat, has_fix_or_perf).
    """
    log_range = f"{base_ref}..HEAD" if base_ref else "HEAD"
    subjects = run_git("log", log_range, "--pretty=%s")
    bodies = run_git("log", log_range, "--pretty=%B")
    has_breaking = bool(
        re.search(r"^[a-z][^:!]*!:", subjects, flags=re.MULTILINE)
        or re.search(r"^BREAKING CHANGE:", bodies, flags=re.MULTILINE),
    )
    has_feat = bool(re.search(r"^feat(\(|:)|^feat!", subjects, flags=re.MULTILINE))
    has_fix_or_perf = bool(
        re.search(r"^(fix|perf)(\(|:)|^(fix|perf)!", subjects, flags=re.MULTILINE),
    )
    return has_breaking, has_feat, has_fix_or_perf


def compute_next_version(
    base_version: str,
    breaking: bool,
    feat: bool,
    fix: bool,
) -> str:
    """Compute the next semantic version based on commit signals.

    Args:
        base_version: Baseline version string.
        breaking: Whether breaking changes were detected.
        feat: Whether features were detected.
        fix: Whether fixes/perf were detected.

    Returns:
        Next semantic version or an empty string if no bump is needed.
    """
    major, minor, patch = parse_semver(base_version)
    if breaking:
        major += 1
        minor = 0
        patch = 0
    elif feat:
        minor += 1
        patch = 0
    elif fix:
        patch += 1
    else:
        return ""
    return f"{major}.{minor}.{patch}"


def clamp_to_minor(
    base_version: str,
    next_version: str,
    max_bump: str | None,
) -> str:
    """Clamp a computed version to a minor bump when required.

    Args:
        base_version: Baseline version string.
        next_version: Computed next version.
        max_bump: Policy value; when ``"minor"``, clamp majors to minor.

    Returns:
        Possibly clamped next version string.
    """
    if not base_version or not next_version:
        return next_version
    if (max_bump or "").lower() != "minor":
        return next_version
    bmaj, bmin, _ = parse_semver(base_version)
    nmaj, _, _ = parse_semver(next_version)
    if nmaj > bmaj:
        return f"{bmaj}.{bmin + 1}.0"
    return next_version


def compute() -> ComputeResult:
    """Compute the next version honoring enterprise release policies.

    Returns:
        ComputeResult with baseline, next version, and detected signals.

    Raises:
        RuntimeError: When no valid baseline tag exists or policy forbids
            an unapproved major release and clamping is not configured.
    """
    # Enterprise policy: tags are the single source of truth.
    # Require an existing v*-prefixed tag as the baseline; fail if missing.
    last_tag = read_last_tag()
    if not last_tag:
        raise RuntimeError(
            "No v*-prefixed release tag found. Tag the last release (e.g., v0.4.0) "
            "before computing the next version.",
        )
    if not TAG_RE.match(last_tag):
        raise RuntimeError(
            f"Baseline tag '{last_tag}' is not a valid v*-prefixed semantic version.",
        )
    base_ref = last_tag
    base_version = last_tag.lstrip("v")

    breaking, feat, fix = detect_commit_types(base_ref)

    # Enterprise gate: allow major bumps only if an explicit label is present
    # on the PR that was merged into main for this commit. If not allowed, we
    # either clamp (when MAX_BUMP=minor) or fail fast with guidance.
    allow_label = os.getenv("ALLOW_MAJOR_LABEL", "allow-major")
    sha = os.getenv("GITHUB_SHA") or run_git("rev-parse", "HEAD")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("RELEASE_TOKEN") or os.getenv("GITHUB_TOKEN") or ""

    major_allowed = False
    if breaking and repo and sha and token:
        owner, name = repo.split("/", 1)
        url = f"https://api.github.com/repos/{owner}/{name}/commits/{sha}/pulls"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        }
        # Query associated PRs for this commit and inspect labels
        try:
            with httpx.Client(timeout=10.0) as client:  # nosec B113
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    pulls = resp.json()
                    for pr in pulls:
                        labels = pr.get("labels", [])
                        names = {str(label.get("name", "")).lower() for label in labels}
                        if allow_label.lower() in names:
                            major_allowed = True
                            break
        except Exception:
            # Network failures should not blow up version computation; we rely
            # on the MAX_BUMP policy or fail below if breaking is unapproved.
            major_allowed = False

    next_version = compute_next_version(base_version, breaking, feat, fix)

    if breaking and not major_allowed:
        # If explicit clamp policy is set, apply it; otherwise fail fast
        max_bump = os.getenv("MAX_BUMP")
        if (max_bump or "").lower() == "minor":
            next_version = clamp_to_minor(base_version, next_version, max_bump)
        else:
            raise RuntimeError(
                "Major bump detected but not permitted. Add the '"
                + allow_label
                + "' label to the PR to allow a major release, or set MAX_BUMP=minor "
                "to clamp majors to minor.",
            )

    next_version = clamp_to_minor(base_version, next_version, os.getenv("MAX_BUMP"))
    return ComputeResult(
        base_ref=base_ref,
        base_version=base_version,
        next_version=next_version,
        has_breaking=breaking,
        has_feat=feat,
        has_fix_or_perf=fix,
    )


def main() -> None:
    """CLI entry to compute and emit the next semantic version.

    Raises:
        SystemExit: With exit code 2 on policy/usage errors.
    """
    parser = argparse.ArgumentParser(
        description="Compute next semantic version and write to GITHUB_OUTPUT",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print next version to stdout instead of writing GITHUB_OUTPUT",
    )
    args = parser.parse_args()

    try:
        result = compute()
    except RuntimeError as exc:
        msg = str(exc)
        print(msg)
        raise SystemExit(2) from None

    print(
        "Base: "
        f"{result.base_ref or '<none>'} "
        f"({result.base_version or 'unknown'})\n"
        "Detected: "
        f"breaking={result.has_breaking} "
        f"feat={result.has_feat} "
        f"fix/perf={result.has_fix_or_perf}",
    )

    if args.print_only or not os.getenv("GITHUB_OUTPUT"):
        print(f"next_version={result.next_version}")
        return

    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"next_version={result.next_version}\n")


if __name__ == "__main__":
    main()
