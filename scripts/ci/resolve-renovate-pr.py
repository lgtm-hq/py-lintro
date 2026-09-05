#!/usr/bin/env python3
"""Resolve the open Renovate PR for the current in-repository branch."""

from __future__ import annotations

import json
import os
import sys
import time

try:
    from github_api import gh_json as _gh_json
except ModuleNotFoundError:
    from scripts.ci.github_api import gh_json as _gh_json

DEFAULT_ATTEMPTS = 6
DEFAULT_DELAY_SECONDS = 10


def resolve_pr(
    *,
    repository: str,
    branch: str,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_seconds: int = DEFAULT_DELAY_SECONDS,
) -> int:
    """Return the unique open PR number for *branch*.

    Args:
        repository: ``owner/name`` repository slug.
        branch: In-repository branch name.
        attempts: Maximum API polls when Renovate has not created the PR yet.
        delay_seconds: Seconds between zero-result API polls.

    Raises:
        RuntimeError: If the API fails or the branch has zero/multiple PRs.
        ValueError: If the retry configuration is invalid.

    Returns:
        The unique open pull-request number.
    """
    owner, _, _ = repository.partition("/")
    if not owner or not branch.startswith("renovate/"):
        raise RuntimeError(f"not an in-repository Renovate branch: {branch!r}")
    if attempts < 1:
        raise ValueError("attempts must be positive")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    query = f"head={owner}:{branch}&base=main&state=open&per_page=100"
    for attempt in range(attempts):
        payload = _gh_json(f"repos/{repository}/pulls?{query}")
        if not isinstance(payload, list):
            raise RuntimeError("GitHub returned a non-list pull-request response")
        numbers: list[int] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            number = item.get("number")
            if isinstance(number, int):
                numbers.append(number)
        if len(numbers) == 1:
            return numbers[0]
        if len(numbers) > 1:
            # Multiple matches indicate a broken branch/PR invariant; retrying
            # cannot make that safe.
            raise RuntimeError(
                f"expected one open PR for {branch!r}, found {len(numbers)}",
            )
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    raise RuntimeError(f"expected one open PR for {branch!r}, found 0")


def main() -> int:
    """Resolve and export the candidate tag."""
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        branch = os.environ["GITHUB_REF_NAME"]
        sha = os.environ["GITHUB_SHA"]
        attempts = int(os.environ.get("RENOVATE_PR_ATTEMPTS", DEFAULT_ATTEMPTS))
        delay_seconds = int(
            os.environ.get("RENOVATE_PR_DELAY_SECONDS", DEFAULT_DELAY_SECONDS),
        )
        number = resolve_pr(
            repository=repository,
            branch=branch,
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
    except (KeyError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    short_sha = sha[:12]
    tag = f"tools-candidate-pr{number}-{short_sha}"
    print(f"Resolved Renovate PR #{number}; candidate tag: {tag}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as output_file:
            output_file.write(f"pr-number={number}\n")
            output_file.write(f"candidate-tag={tag}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
