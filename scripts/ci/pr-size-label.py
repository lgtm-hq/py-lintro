#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Label a pull request with a size class from effective changed lines.

Computes an *effective* line count from the PR file list (GitHub API), excluding
lockfiles and generated paths, optionally excluding tests when the PR also
touches non-test source, and weighting ``docs/**`` at 0.5. Applies exactly one
``size:*`` label and removes stale size labels.

Environment:
    PR_NUMBER: Pull request number (required).
    GITHUB_REPOSITORY: ``owner/repo`` (required; provided by Actions).
    GITHUB_TOKEN / GH_TOKEN: Token with pull-request write (and issues write to
        upsert missing size labels).

Usage:
    PR_NUMBER=123 python3 scripts/ci/pr-size-label.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - subprocess invokes fixed ``gh`` argv lists only
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Tunable thresholds: label applies when effective lines are <= the bound.
# Anything above the last bound is XL.
SIZE_THRESHOLDS: tuple[tuple[str, int], ...] = (
    ("size:XS", 20),
    ("size:S", 100),
    ("size:M", 400),
    ("size:L", 1000),
)
SIZE_XL = "size:XL"
SIZE_LABEL_NAMES: frozenset[str] = frozenset(
    (*(name for name, _ in SIZE_THRESHOLDS), SIZE_XL),
)

# Canonical label metadata (also the sync source for upsert).
SIZE_LABEL_DEFS: tuple[tuple[str, str, str], ...] = (
    ("size:XS", "0e8a16", "XS: <=20 effective changed lines"),
    ("size:S", "5ebd3e", "S: 21-100 effective changed lines"),
    ("size:M", "fbca04", "M: 101-400 effective changed lines"),
    ("size:L", "fe7d37", "L: 401-1000 effective changed lines"),
    ("size:XL", "d93f0b", "XL: >1000 effective changed lines"),
)

DOCS_WEIGHT = 0.5

_LOCKFILE_BASENAMES: frozenset[str] = frozenset(
    {
        "uv.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "bun.lock",
        "bun.lockb",
    },
)

_GENERATED_PATHS: frozenset[str] = frozenset(
    {
        "lintro/tools/manifest.json",
    },
)

_TEST_DIR_SEGMENT = re.compile(r"(^|/)(tests?|__tests__)(/|$)")
_TEST_FILENAME = re.compile(r"(^|/)test_[^/]+\.py$")


@dataclass(frozen=True, slots=True)
class FileDiff:
    """One changed file with addition/deletion counts from the Files API."""

    path: str
    additions: int
    deletions: int

    @property
    def changed_lines(self) -> int:
        """Return additions plus deletions for this file."""
        return self.additions + self.deletions


def is_lockfile(*, path: str) -> bool:
    """Return whether ``path`` is a known lockfile basename."""
    return Path(path).name in _LOCKFILE_BASENAMES


def is_generated(*, path: str) -> bool:
    """Return whether ``path`` is a generated artifact excluded from sizing."""
    return path in _GENERATED_PATHS


def is_test_path(*, path: str) -> bool:
    """Return whether ``path`` looks like a test file or lives under tests/."""
    normalized = path.replace("\\", "/")
    return bool(
        _TEST_DIR_SEGMENT.search(normalized) or _TEST_FILENAME.search(normalized),
    )


def is_docs_path(*, path: str) -> bool:
    """Return whether ``path`` is under ``docs/``."""
    normalized = path.replace("\\", "/")
    return normalized == "docs" or normalized.startswith("docs/")


def is_excluded_entirely(*, path: str) -> bool:
    """Return whether ``path`` contributes zero lines to the effective count."""
    return is_lockfile(path=path) or is_generated(path=path)


def has_non_test_source(*, files: Sequence[FileDiff]) -> bool:
    """Return True when any file is countable non-test content.

    Lockfiles and generated paths do not count as non-test source. Docs do —
    a docs+tests PR excludes tests the same way a source+tests PR does.
    """
    return any(
        not is_excluded_entirely(path=f.path) and not is_test_path(path=f.path)
        for f in files
    )


def file_effective_weight(*, path: str, exclude_tests: bool) -> float | None:
    """Return the line-weight multiplier for ``path``, or None to skip it.

    Args:
        path: Repository-relative path.
        exclude_tests: When True, test paths are skipped entirely.

    Returns:
        ``None`` to exclude the file, otherwise a positive weight (1.0 or
        ``DOCS_WEIGHT``).
    """
    if is_excluded_entirely(path=path):
        return None
    if exclude_tests and is_test_path(path=path):
        return None
    if is_docs_path(path=path):
        return DOCS_WEIGHT
    return 1.0


def compute_effective_lines(*, files: Sequence[FileDiff]) -> float:
    """Compute effective changed lines for sizing.

    Args:
        files: Per-file addition/deletion rows from the PR Files API.

    Returns:
        Weighted sum of changed lines after exclusions.
    """
    exclude_tests = has_non_test_source(files=files)
    total = 0.0
    for file_diff in files:
        weight = file_effective_weight(
            path=file_diff.path,
            exclude_tests=exclude_tests,
        )
        if weight is None:
            continue
        total += file_diff.changed_lines * weight
    return total


def size_label_for_lines(*, effective_lines: float) -> str:
    """Map an effective line count to a ``size:*`` label.

    Args:
        effective_lines: Weighted changed-line total.

    Returns:
        One of ``size:XS`` … ``size:XL``.
    """
    for name, upper in SIZE_THRESHOLDS:
        if effective_lines <= upper:
            return name
    return SIZE_XL


def stale_size_labels(*, current_labels: Sequence[str], desired: str) -> list[str]:
    """Return size labels present on the PR that are not ``desired``.

    Args:
        current_labels: Labels currently on the issue/PR.
        desired: The single size label that should remain.

    Returns:
        Names of size labels to remove.
    """
    return [
        name for name in current_labels if name in SIZE_LABEL_NAMES and name != desired
    ]


def labels_to_add(*, current_labels: Sequence[str], desired: str) -> list[str]:
    """Return the desired size label when it is not already present.

    Args:
        current_labels: Labels currently on the issue/PR.
        desired: The size label that should be applied.

    Returns:
        Either ``[desired]`` or an empty list.
    """
    if desired in current_labels:
        return []
    return [desired]


def parse_pr_files_payload(*, payload: Sequence[dict[str, object]]) -> list[FileDiff]:
    """Convert GitHub Files API JSON objects into ``FileDiff`` rows.

    Args:
        payload: Decoded JSON array from ``GET .../pulls/{n}/files``.

    Returns:
        FileDiff list (binary files with null counts become 0/0).
    """
    results: list[FileDiff] = []
    for item in payload:
        path = str(item.get("filename", ""))
        if not path:
            continue
        additions = item.get("additions", 0)
        deletions = item.get("deletions", 0)
        results.append(
            FileDiff(
                path=path,
                additions=int(additions) if isinstance(additions, int) else 0,
                deletions=int(deletions) if isinstance(deletions, int) else 0,
            ),
        )
    return results


@lru_cache(maxsize=1)
def _gh_bin() -> str:
    """Resolve the absolute path to the ``gh`` CLI.

    Returns:
        Absolute path to ``gh``.

    Raises:
        RuntimeError: When ``gh`` is not on ``PATH``.
    """
    path = shutil.which("gh")
    if path is None:
        msg = "gh CLI not found on PATH"
        raise RuntimeError(msg)
    return path


def _run_gh(
    *,
    args: Sequence[str],
    check: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` with a fixed argv list (absolute binary path).

    Args:
        args: Arguments after ``gh``.
        check: When True, raise on non-zero exit.
        stdin: Optional stdin payload.

    Returns:
        Completed process with captured stdout/stderr.

    Raises:
        RuntimeError: When ``check`` is True and ``gh`` exits non-zero.
    """
    completed = (
        subprocess.run(  # nosec B603 - absolute gh path, fixed argv, shell=False
            [_gh_bin(), *args],
            check=False,
            capture_output=True,
            text=True,
            input=stdin,
        )
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip()
        msg = f"gh {' '.join(args)} failed ({completed.returncode}): {stderr}"
        raise RuntimeError(msg)
    return completed


def _decode_paginated_json_objects(*, raw: str) -> list[dict[str, object]]:
    """Decode one or more JSON values from ``gh api --paginate`` stdout.

    Args:
        raw: Concatenated JSON arrays/objects from paginated ``gh api``.

    Returns:
        Flattened list of JSON objects.
    """
    chunks: list[dict[str, object]] = []
    decoder = json.JSONDecoder()
    idx = 0
    text = raw.strip()
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            parsed, offset = decoder.raw_decode(text, idx)
        except json.JSONDecodeError as exc:
            msg = f"failed to decode GitHub API response: {exc}"
            raise RuntimeError(msg) from exc
        idx = offset
        if isinstance(parsed, list):
            chunks.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            chunks.append(parsed)
    return chunks


def _fetch_pr_files(*, repository: str, pr_number: int) -> list[FileDiff]:
    """Fetch per-file diffs for a PR via the GitHub API."""
    raw = _run_gh(
        args=[
            "api",
            f"repos/{repository}/pulls/{pr_number}/files",
            "--paginate",
        ],
    ).stdout
    return parse_pr_files_payload(
        payload=_decode_paginated_json_objects(raw=raw),
    )


def _fetch_current_labels(*, repository: str, pr_number: int) -> list[str]:
    """List label names currently on the PR (issue labels endpoint)."""
    raw = _run_gh(
        args=[
            "api",
            f"repos/{repository}/issues/{pr_number}/labels",
            "--paginate",
        ],
    ).stdout
    return [
        str(item["name"])
        for item in _decode_paginated_json_objects(raw=raw)
        if "name" in item
    ]


def _ensure_size_labels(*, repository: str) -> None:
    """Create or update the five managed size labels (best-effort)."""
    for name, color, description in SIZE_LABEL_DEFS:
        # Prefer create; on failure (already exists) update color/description.
        create = _run_gh(
            args=[
                "api",
                "--method",
                "POST",
                f"repos/{repository}/labels",
                "-f",
                f"name={name}",
                "-f",
                f"color={color}",
                "-f",
                f"description={description}",
            ],
            check=False,
        )
        if create.returncode == 0:
            continue
        encoded = name.replace(":", "%3A")
        update = _run_gh(
            args=[
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/labels/{encoded}",
                "-f",
                f"color={color}",
                "-f",
                f"description={description}",
            ],
            check=False,
        )
        if update.returncode != 0:
            print(
                f"warning: could not upsert label {name}: "
                f"{update.stderr.strip() or create.stderr.strip()}",
                file=sys.stderr,
            )


def _apply_labels(
    *,
    repository: str,
    pr_number: int,
    to_add: Sequence[str],
    to_remove: Sequence[str],
) -> None:
    """Remove stale size labels then add the desired one."""
    for name in to_remove:
        encoded = name.replace(":", "%3A")
        removed = _run_gh(
            args=[
                "api",
                "--method",
                "DELETE",
                f"repos/{repository}/issues/{pr_number}/labels/{encoded}",
            ],
            check=False,
        )
        if removed.returncode != 0 and "Not Found" not in (
            removed.stderr + removed.stdout
        ):
            msg = (
                f"failed to remove label {name}: "
                f"{removed.stderr.strip() or removed.stdout.strip()}"
            )
            raise RuntimeError(msg)

    if not to_add:
        return
    body = json.dumps({"labels": list(to_add)})
    _run_gh(
        args=[
            "api",
            "--method",
            "POST",
            f"repos/{repository}/issues/{pr_number}/labels",
            "--input",
            "-",
        ],
        stdin=body,
    )


def classify_and_plan(
    *,
    files: Sequence[FileDiff],
    current_labels: Sequence[str],
) -> tuple[float, str, list[str], list[str]]:
    """Pure classification: effective lines, desired label, add/remove lists.

    Args:
        files: PR file diffs.
        current_labels: Labels currently on the PR.

    Returns:
        Tuple of (effective_lines, desired_label, to_add, to_remove).
    """
    effective = compute_effective_lines(files=files)
    desired = size_label_for_lines(effective_lines=effective)
    to_remove = stale_size_labels(current_labels=current_labels, desired=desired)
    to_add = labels_to_add(current_labels=current_labels, desired=desired)
    return effective, desired, to_add, to_remove


def main() -> int:
    """Fetch PR diffs, classify size, and sync ``size:*`` labels."""
    pr_raw = os.environ.get("PR_NUMBER", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not pr_raw or not repository:
        print(
            "PR_NUMBER and GITHUB_REPOSITORY environment variables are required.",
            file=sys.stderr,
        )
        return 1
    try:
        pr_number = int(pr_raw)
    except ValueError:
        print(f"PR_NUMBER must be an integer, got {pr_raw!r}.", file=sys.stderr)
        return 1

    # Prefer GH_TOKEN when Actions only sets GITHUB_TOKEN.
    if not os.environ.get("GH_TOKEN") and os.environ.get("GITHUB_TOKEN"):
        os.environ["GH_TOKEN"] = os.environ["GITHUB_TOKEN"]

    _ensure_size_labels(repository=repository)
    files = _fetch_pr_files(repository=repository, pr_number=pr_number)
    current = _fetch_current_labels(repository=repository, pr_number=pr_number)
    effective, desired, to_add, to_remove = classify_and_plan(
        files=files,
        current_labels=current,
    )
    print(
        f"PR #{pr_number}: {len(files)} files, "
        f"{effective:.1f} effective lines -> {desired}",
    )
    if to_remove:
        print(f"Removing stale labels: {', '.join(to_remove)}")
    if to_add:
        print(f"Adding labels: {', '.join(to_add)}")
    else:
        print(f"Label {desired} already present")
    _apply_labels(
        repository=repository,
        pr_number=pr_number,
        to_add=to_add,
        to_remove=to_remove,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
