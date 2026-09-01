#!/usr/bin/env python3
"""Classify a main tools update and export its candidate tag when applicable.

The PR head changes when the digest commit is pushed, so promotion resolves
the latest candidate tag for the merged PR number rather than reconstructing a
tag from the post-merge commit SHA.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - fixed gh argv with validated values
import sys
from datetime import UTC, datetime
from typing import Any

CANDIDATE_RE = re.compile(
    r"^tools-candidate-pr(?P<number>[1-9][0-9]*)-[0-9a-f]{7,40}$",
)
PACKAGE = "lintro-tools"
# Keep this set in lockstep with docker-tools-candidate.yml. These are the
# only Renovate paths that can change the tools image being built.
CANDIDATE_PATHS = frozenset(
    {
        "docker/tools.Dockerfile",
        "lintro/_tool_versions.py",
        "lintro/tools/manifest.src.json",
        "lintro/_tool_packages.py",
        "package.json",
        "pyproject.toml",
        "requirements-semgrep.txt",
    },
)
BUILD_PATHS = frozenset(
    {
        *CANDIDATE_PATHS,
        "scripts/utils/install-tools.sh",
        "scripts/utils/install-semgrep.sh",
        "scripts/utils/utils.sh",
        ".github/workflows/docker-tools-publish.yml",
    },
)
CONSUMER_PATHS = frozenset({"Dockerfile", "docker/ai-tools.Dockerfile"})


def _gh_json(*args: str) -> object:
    """Run a fixed ``gh api`` command and decode JSON."""
    result = subprocess.run(  # nosec B603, B607 - fixed gh executable and flags
        ["gh", "api", *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": os.environ.get("GITHUB_TOKEN", "")},
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh api failed")
    return json.loads(result.stdout)


def _pages(payload: object) -> list[dict[str, Any]]:
    """Flatten a ``gh api --paginate --slurp`` response."""
    if not isinstance(payload, list):
        raise RuntimeError("GitHub returned a non-list package response")
    entries: list[dict[str, Any]] = []
    for page in payload:
        if not isinstance(page, list):
            raise RuntimeError("GitHub returned a malformed package page")
        entries.extend(item for item in page if isinstance(item, dict))
    return entries


def _merged_pr(*, repository: str, merge_sha: str) -> dict[str, Any] | None:
    """Return the PR associated with a merge commit, when there is one."""
    payload = _gh_json(f"repos/{repository}/commits/{merge_sha}/pulls")
    if not isinstance(payload, list):
        raise RuntimeError("GitHub returned a non-list merged-PR response")
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("number"), int):
            return item
    return None


def _is_renovate_pr(pr: dict[str, Any]) -> bool:
    """Identify Renovate PRs without trusting mutable title or body text."""
    user = pr.get("user")
    head = pr.get("head")
    author = user.get("login") if isinstance(user, dict) else None
    branch = head.get("ref") if isinstance(head, dict) else None
    return author == "renovate[bot]" and (
        isinstance(branch, str) and branch.startswith("renovate/")
    )


def _pull_request_files(*, repository: str, pr_number: int) -> set[str]:
    """Return all changed paths for a pull request."""
    payload = _gh_json(
        f"repos/{repository}/pulls/{pr_number}/files?per_page=100",
        "--paginate",
        "--slurp",
    )
    files = _pages(payload)
    return {
        filename for file in files if isinstance(filename := file.get("filename"), str)
    }


def _has_build_path(paths: set[str]) -> bool:
    """Return whether changed files can affect the tools image build."""
    return any(
        path in BUILD_PATHS or path.startswith("lintro_build/") for path in paths
    )


def _is_consumer_only(paths: set[str]) -> bool:
    """Return whether a PR only changes downstream image consumer pins."""
    return bool(paths) and paths.issubset(CONSUMER_PATHS)


def _candidate_tag_for_pr(*, repository: str, pr_number: int) -> str | None:
    """Return the newest candidate tag for a PR number."""
    versions = _pages(
        _gh_json(
            f"orgs/{repository.split('/', 1)[0]}/packages/container/"
            f"{PACKAGE}/versions?per_page=100",
            "--paginate",
            "--slurp",
        ),
    )
    candidates: list[tuple[datetime, str]] = []
    for version in versions:
        updated_at = version.get("updated_at")
        metadata = version.get("metadata")
        tags = (
            metadata.get("container", {}).get("tags", [])
            if isinstance(metadata, dict)
            else []
        )
        if not isinstance(updated_at, str) or not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, str):
                continue
            match = CANDIDATE_RE.fullmatch(tag)
            if match is None or int(match.group("number")) != pr_number:
                continue
            try:
                timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            candidates.append((timestamp.astimezone(UTC), tag))
    if not candidates:
        return None
    return max(candidates)[1]


def resolve_candidate_tag(*, repository: str, merge_sha: str) -> str | None:
    """Return the newest candidate tag for the PR merged by *merge_sha*."""
    pr = _merged_pr(repository=repository, merge_sha=merge_sha)
    if pr is None:
        return None
    return _candidate_tag_for_pr(
        repository=repository,
        pr_number=pr["number"],
    )


def resolve_main_action(*, repository: str, merge_sha: str) -> tuple[str, str | None]:
    """Classify a main push as candidate promotion or canonical publication.

    A merged Renovate PR without a candidate is an error rather than a signal
    to rebuild: rebuilding would violate the no-rebuild promotion guarantee.
    Non-Renovate merges and direct pushes are safe canonical-build fallbacks.
    """
    pr = _merged_pr(repository=repository, merge_sha=merge_sha)
    if pr is None or not _is_renovate_pr(pr):
        if pr is None:
            return "publish", None
        paths = _pull_request_files(
            repository=repository,
            pr_number=pr["number"],
        )
        return ("skip", None) if _is_consumer_only(paths) else ("publish", None)
    pr_number = pr["number"]
    paths = _pull_request_files(repository=repository, pr_number=pr_number)
    if CANDIDATE_PATHS.isdisjoint(paths):
        if _is_consumer_only(paths):
            # A consumer digest PR only changes Dockerfile FROM pins. It does
            # not need a candidate image and must not be mistaken for a lost one.
            return "skip", None
        if _has_build_path(paths):
            # Renovate can update an installer/build input that is deliberately
            # outside the manifest candidate trigger; rebuild canonically.
            return "publish", None
        # Be conservative for an unexpected Renovate file set. The main trigger
        # is broad enough that this is preferable to silently doing nothing.
        return "publish", None
    tag = _candidate_tag_for_pr(repository=repository, pr_number=pr_number)
    if tag is None:
        raise RuntimeError(
            f"merged Renovate PR #{pr_number} has no candidate image; "
            "refusing a fallback rebuild",
        )
    return "promote", tag


def main() -> int:
    """Resolve and export the promotion source tag."""
    try:
        action, tag = resolve_main_action(
            repository=os.environ["GITHUB_REPOSITORY"],
            merge_sha=os.environ["GITHUB_SHA"],
        )
    except (KeyError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if action == "promote":
        message = f"Promote candidate {tag}"
    elif action == "skip":
        message = "Skip consumer-only digest update"
    else:
        message = "Publish canonical tools image"
    print(message)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as output_file:  # noqa: PTH123
            output_file.write(f"action={action}\n")
            output_file.write(f"candidate-tag={tag or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
