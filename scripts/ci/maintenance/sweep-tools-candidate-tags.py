#!/usr/bin/env python3
"""Delete stale or closed-unmerged ``lintro-tools`` candidate versions."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    from github_api import gh_json as _gh_json
except ModuleNotFoundError:
    from scripts.ci.github_api import gh_json as _gh_json

PACKAGE = "lintro-tools"
CANDIDATE_RE = re.compile(
    r"^tools-candidate-pr(?P<number>[1-9][0-9]*)-[0-9a-f]{7,40}$",
)
EPHEMERAL_RE = re.compile(
    r"^(?:tools-candidate-pr[1-9][0-9]*-[0-9a-f]{7,40}|sha-|renovate-)",
)


@dataclass(frozen=True)
class CandidateVersion:
    """A GHCR version carrying a candidate tag."""

    version_id: str
    tags: tuple[str, ...]
    updated_at: datetime
    pr_numbers: tuple[int, ...] = ()
    # Kept as a construction/read compatibility shim for callers of the
    # original single-PR helper. Parsed versions always populate pr_numbers.
    pr_number: int | None = None

    def __post_init__(self) -> None:
        """Normalize legacy and multi-PR construction into distinct numbers."""
        numbers = self.pr_numbers or (
            (self.pr_number,) if self.pr_number is not None else ()
        )
        normalized = tuple(dict.fromkeys(numbers))
        if not normalized:
            raise ValueError("candidate version must reference at least one PR")
        object.__setattr__(self, "pr_numbers", normalized)
        object.__setattr__(self, "pr_number", normalized[0])


def parse_timestamp(value: str) -> datetime:
    """Parse GitHub's UTC timestamp representation."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def candidate_version(payload: dict[str, Any]) -> CandidateVersion | None:
    """Parse a package-version response when it is safe to sweep."""
    version_id = payload.get("id")
    updated_at = payload.get("updated_at")
    metadata = payload.get("metadata")
    container = metadata.get("container") if isinstance(metadata, dict) else None
    tags = container.get("tags") if isinstance(container, dict) else None
    if not isinstance(version_id, (str, int)) or not isinstance(updated_at, str):
        return None
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return None
    candidate_tags = [tag for tag in tags if CANDIDATE_RE.fullmatch(tag)]
    if not candidate_tags or any(not EPHEMERAL_RE.match(tag) for tag in tags):
        # Deleting a GHCR version deletes every tag on that digest. Never
        # delete a candidate version that was promoted or otherwise acquired
        # a persistent tag.
        return None
    pr_numbers = tuple(
        sorted(
            {
                int(match.group("number"))
                for tag in candidate_tags
                if (match := CANDIDATE_RE.fullmatch(tag)) is not None
            },
        ),
    )
    if not pr_numbers:
        return None
    try:
        timestamp = parse_timestamp(updated_at)
    except ValueError:
        return None
    return CandidateVersion(
        version_id=str(version_id),
        tags=tuple(tags),
        updated_at=timestamp,
        pr_numbers=pr_numbers,
    )


def should_delete(
    candidate: CandidateVersion,
    *,
    now: datetime,
    pr_states: Mapping[int, tuple[str | None, str | None]] | None = None,
    pr_state: str | None = None,
    merged_at: str | None = None,
    min_age_days: int,
) -> bool:
    """Return whether age or closed-unmerged state makes a version deletable."""
    if now - candidate.updated_at >= timedelta(days=min_age_days):
        return True
    if pr_states is None:
        if pr_state is None:
            return False
        pr_states = dict.fromkeys(candidate.pr_numbers, (pr_state, merged_at))
    return (
        bool(pr_states)
        and all(
            state == "closed" and not merged for state, merged in pr_states.values()
        )
        and all(number in pr_states for number in candidate.pr_numbers)
    )


def _gh_json_allow_not_found(*args: str) -> tuple[bool, object]:
    """Run ``gh api``, treating a concurrent package removal as benign."""
    try:
        return True, _gh_json(*args)
    except RuntimeError as exc:
        if "404" in str(exc):
            return False, None
        raise


def _package_versions(*, owner: str) -> list[dict[str, Any]]:
    """List all package versions, preserving pagination."""
    payload = _gh_json(
        f"orgs/{owner}/packages/container/{PACKAGE}/versions?per_page=100",
        "--paginate",
        "--slurp",
    )
    if not isinstance(payload, list):
        raise RuntimeError("GitHub returned a malformed package-version response")
    entries: list[dict[str, Any]] = []
    for page in payload:
        if not isinstance(page, list):
            raise RuntimeError("GitHub returned a malformed package-version page")
        entries.extend(item for item in page if isinstance(item, dict))
    return entries


def _pull_request(*, repository: str, number: int) -> tuple[str | None, str | None]:
    """Return a PR's state and merge timestamp."""
    payload = _gh_json(f"repos/{repository}/pulls/{number}")
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned a malformed pull-request response")
    state = payload.get("state")
    merged_at = payload.get("merged_at")
    return (
        state if isinstance(state, str) else None,
        merged_at if isinstance(merged_at, str) else None,
    )


def _pull_request_states(
    *,
    repository: str,
    candidate: CandidateVersion,
) -> dict[int, tuple[str | None, str | None]]:
    """Return state and merge timestamp for every PR owning candidate tags."""
    return {
        number: _pull_request(repository=repository, number=number)
        for number in candidate.pr_numbers
    }


def _refresh_candidate(
    *,
    owner: str,
    candidate: CandidateVersion,
) -> CandidateVersion | None:
    """Re-read a package version immediately before a destructive delete."""
    endpoint = (
        f"orgs/{owner}/packages/container/{PACKAGE}/versions/" f"{candidate.version_id}"
    )
    found, payload = _gh_json_allow_not_found(endpoint)
    if not found:
        return None
    if not isinstance(payload, dict):
        return None
    return candidate_version(payload)


def main() -> int:
    """Sweep candidate versions according to environment configuration."""
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("GH_TOKEN is required", file=sys.stderr)
        return 2
    try:
        min_age_days = int(os.environ.get("MIN_AGE_DAYS", "14"))
    except ValueError:
        print("MIN_AGE_DAYS must be an integer", file=sys.stderr)
        return 2
    if min_age_days < 1:
        print("MIN_AGE_DAYS must be positive", file=sys.stderr)
        return 2
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    repository = os.environ.get("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    owner = repository.split("/", 1)[0]
    now = datetime.now(UTC)

    try:
        candidates = [
            parsed
            for payload in _package_versions(owner=owner)
            if (parsed := candidate_version(payload)) is not None
        ]
        for candidate in candidates:
            pr_states = _pull_request_states(
                repository=repository,
                candidate=candidate,
            )
            if not should_delete(
                candidate,
                now=now,
                pr_states=pr_states,
                min_age_days=min_age_days,
            ):
                continue
            # Eligibility is evaluated on the initial listing first. Refresh
            # only now, immediately before reporting/deleting, to close the
            # TOCTOU window for a newly-added persistent tag.
            refreshed = _refresh_candidate(owner=owner, candidate=candidate)
            if refreshed is None:
                print(
                    f"Skipping {candidate.version_id}: package tags changed "
                    "or the version was removed",
                )
                continue
            pr_states = _pull_request_states(
                repository=repository,
                candidate=refreshed,
            )
            if not should_delete(
                refreshed,
                now=now,
                pr_states=pr_states,
                min_age_days=min_age_days,
            ):
                continue
            endpoint = (
                f"orgs/{owner}/packages/container/{PACKAGE}/versions/"
                f"{refreshed.version_id}"
            )
            if dry_run:
                print(
                    f"[dry-run] Would delete {endpoint} "
                    f"(tags: {', '.join(refreshed.tags)})",
                )
            else:
                found, _ = _gh_json_allow_not_found("--method", "DELETE", endpoint)
                if not found:
                    print(f"Skipping {endpoint}: version was already removed")
                    continue
                print(f"Deleted {endpoint} (tags: {', '.join(refreshed.tags)})")
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
