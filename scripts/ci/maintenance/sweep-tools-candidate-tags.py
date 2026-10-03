#!/usr/bin/env python3
"""Delete stale or closed-unmerged ``lintro-tools`` candidate versions.

Collect protected digests once (default branch plus open ``renovate/*``
heads). Before each delete, re-read only the default-branch pins. Any
collection or re-check failure aborts remaining deletions. ``pinned-*``
tags are not candidates; there is no retain-N of old promoted versions.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from github_api import gh_json as _gh_json
except ModuleNotFoundError as exc:
    if exc.name != "github_api":
        raise
    # Direct production invocations put this maintenance directory on
    # sys.path, not the repository root. Add the root before importing the
    # shared helper as a package (``python3 scripts/ci/...``).
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts.ci.github_api import gh_json as _gh_json

PACKAGE = "lintro-tools"
PACKAGE_REF = "ghcr.io/lgtm-hq/lintro-tools"
CANDIDATE_RE = re.compile(
    r"^tools-candidate-pr(?P<number>[1-9][0-9]*)-[0-9a-f]{7,40}$",
)
EPHEMERAL_RE = re.compile(
    r"^(?:tools-candidate-pr[1-9][0-9]*-[0-9a-f]{7,40}|sha-|renovate-)",
)
DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
PINNED_DIGEST_RE = re.compile(
    rf"{re.escape(PACKAGE_REF)}(?::[^\s@]+)?@(sha256:[a-f0-9]{{64}})",
    flags=re.IGNORECASE,
)
ROOT_DOCKERFILE = "Dockerfile"
DOCKER_DIR = "docker"
PAGE_SIZE = 100
RENOVATE_REF_PREFIX = "renovate/"


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
    digest: str = ""

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
    name = payload.get("name")
    container = metadata.get("container") if isinstance(metadata, dict) else None
    tags = container.get("tags") if isinstance(container, dict) else None
    if not isinstance(version_id, (str, int)) or not isinstance(updated_at, str):
        return None
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return None
    digest = name if isinstance(name, str) and DIGEST_RE.fullmatch(name) else ""
    candidate_tags = [tag for tag in tags if CANDIDATE_RE.fullmatch(tag)]
    if not candidate_tags or any(not EPHEMERAL_RE.match(tag) for tag in tags):
        # Deleting a GHCR version deletes every tag on that digest. Never
        # delete a candidate version that was promoted or otherwise acquired
        # a persistent tag. pinned-* is persistent (#2845).
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
        digest=digest,
    )


def _gh_json_allow_not_found(*args: str) -> tuple[bool, object]:
    """Run ``gh api``, treating a concurrent package removal as benign."""
    try:
        return True, _gh_json(*args)
    except RuntimeError as exc:
        if re.search(r"\bHTTP\s+404\b", str(exc), flags=re.IGNORECASE):
            return False, None
        raise


def should_delete(
    candidate: CandidateVersion,
    *,
    now: datetime,
    pr_states: Mapping[int, tuple[str | None, str | None]] | None = None,
    pr_state: str | None = None,
    merged_at: str | None = None,
    min_age_days: int,
) -> bool:
    """Return whether age or closed-unmerged state makes a version deletable.

    Both rules require every owning PR to be known. A missing or unparseable
    PR (state ``None``) leaves the candidate alone even once it is older than
    ``min_age_days``: an orphaned tag is a signal for a human, not a delete.
    """
    if pr_states is None:
        if pr_state is None:
            return False
        pr_states = dict.fromkeys(candidate.pr_numbers, (pr_state, merged_at))
    if not pr_states or any(
        number not in pr_states or pr_states[number][0] is None
        for number in candidate.pr_numbers
    ):
        return False
    if now - candidate.updated_at >= timedelta(days=min_age_days):
        return True
    return all(state == "closed" and not merged for state, merged in pr_states.values())


class ProtectionCollectionError(RuntimeError):
    """Raised when protected-digest collection is incomplete or failed."""


def _flatten_pages(payload: object, *, endpoint: str) -> list[dict[str, Any]]:
    """Flatten a ``gh api --paginate --slurp`` list-of-pages response."""
    if not isinstance(payload, list):
        raise ProtectionCollectionError(
            f"GitHub returned a malformed paginated response for {endpoint}",
        )
    entries: list[dict[str, Any]] = []
    for page in payload:
        if not isinstance(page, list):
            raise ProtectionCollectionError(
                f"GitHub returned a malformed page for {endpoint}",
            )
        entries.extend(item for item in page if isinstance(item, dict))
    return entries


def _confirm_pagination_complete(*, endpoint: str, pages: object) -> None:
    """Fail closed when a full last page may mean a missed page."""
    if not isinstance(pages, list) or not pages:
        return
    last = pages[-1]
    if not isinstance(last, list) or len(last) < PAGE_SIZE:
        return
    next_page = len(pages) + 1
    separator = "&" if "?" in endpoint else "?"
    extra = _gh_json(f"{endpoint}{separator}page={next_page}")
    if extra:
        raise ProtectionCollectionError(
            f"incomplete pagination for {endpoint}: page {next_page} is not empty",
        )


def _paginated_dicts(*, endpoint: str) -> list[dict[str, Any]]:
    """Return every page of a list endpoint, or fail closed."""
    paged = f"{endpoint}{'&' if '?' in endpoint else '?'}per_page={PAGE_SIZE}"
    payload = _gh_json(paged, "--paginate", "--slurp")
    entries = _flatten_pages(payload, endpoint=endpoint)
    _confirm_pagination_complete(endpoint=paged, pages=payload)
    return entries


def _package_versions(*, owner: str) -> list[dict[str, Any]]:
    """List all package versions, preserving pagination."""
    endpoint = f"orgs/{owner}/packages/container/{PACKAGE}/versions"
    try:
        return _paginated_dicts(endpoint=endpoint)
    except ProtectionCollectionError as exc:
        raise RuntimeError(str(exc)) from exc


def _decode_contents(*, payload: object, path: str) -> str:
    """Decode a GitHub contents API file payload."""
    if not isinstance(payload, dict):
        raise ProtectionCollectionError(f"malformed contents response for {path}")
    if payload.get("type") != "file":
        raise ProtectionCollectionError(f"{path} is not a file")
    encoding = payload.get("encoding")
    content = payload.get("content")
    if encoding != "base64" or not isinstance(content, str):
        raise ProtectionCollectionError(f"unreadable contents for {path}")
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtectionCollectionError(f"could not decode {path}") from exc


def _extract_pinned_digests(text: str) -> set[str]:
    """Return ``lintro-tools`` digests referenced in Dockerfile text."""
    return {match.group(1).lower() for match in PINNED_DIGEST_RE.finditer(text)}


def _dockerfile_paths(*, repository: str, ref: str, required: bool) -> list[str]:
    """Return consumer Dockerfile paths at *ref*."""
    paths = [ROOT_DOCKERFILE]
    found, payload = _gh_json_allow_not_found(
        f"repos/{repository}/contents/{DOCKER_DIR}?ref={ref}",
    )
    if not found:
        if required:
            raise ProtectionCollectionError(
                f"missing {DOCKER_DIR}/ on {repository}@{ref}",
            )
        print(f"Skipping {DOCKER_DIR}/ at {ref}: contents API returned 404")
        return paths
    if not isinstance(payload, list):
        raise ProtectionCollectionError(
            f"malformed {DOCKER_DIR}/ listing on {repository}@{ref}",
        )
    for entry in payload:
        if not isinstance(entry, dict):
            raise ProtectionCollectionError(
                f"malformed {DOCKER_DIR}/ entry on {repository}@{ref}",
            )
        name = entry.get("name")
        path = entry.get("path")
        entry_type = entry.get("type")
        if (
            entry_type != "file"
            or not isinstance(name, str)
            or not isinstance(path, str)
        ):
            continue
        if name.endswith(".Dockerfile"):
            paths.append(path)
    return paths


def _digests_at_ref(*, repository: str, ref: str, required: bool) -> set[str]:
    """Collect ``lintro-tools`` pin digests from Dockerfiles at *ref*."""
    digests: set[str] = set()
    for path in _dockerfile_paths(
        repository=repository,
        ref=ref,
        required=required,
    ):
        found, payload = _gh_json_allow_not_found(
            f"repos/{repository}/contents/{path}?ref={ref}",
        )
        if not found:
            if required:
                raise ProtectionCollectionError(
                    f"missing {path} on {repository}@{ref}",
                )
            print(f"Skipping {path} at {ref}: contents API returned 404")
            continue
        digests.update(
            _extract_pinned_digests(_decode_contents(payload=payload, path=path)),
        )
    return digests


def _default_branch(*, repository: str) -> str:
    """Return the repository default branch name."""
    payload = _gh_json(f"repos/{repository}")
    if not isinstance(payload, dict):
        raise ProtectionCollectionError("malformed repository response")
    branch = payload.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise ProtectionCollectionError("repository is missing default_branch")
    return branch


def _open_pull_heads(*, repository: str) -> list[str]:
    """Return head SHAs for open Renovate digest PRs."""
    pulls = _paginated_dicts(endpoint=f"repos/{repository}/pulls?state=open")
    heads: list[str] = []
    for pull in pulls:
        head = pull.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        ref = head.get("ref") if isinstance(head, dict) else None
        if not isinstance(ref, str) or not ref.startswith(RENOVATE_REF_PREFIX):
            continue
        if not isinstance(sha, str) or not sha:
            raise ProtectionCollectionError("open pull request is missing head SHA")
        heads.append(sha)
    return heads


def collect_protected_digests(*, repository: str) -> set[str]:
    """Collect digests pinned on main or open ``renovate/*`` PR heads.

    Args:
        repository: ``owner/name`` of the consumer repository.

    Returns:
        Normalized ``sha256:<hex>`` digests that must not be deleted.

    Raises:
        ProtectionCollectionError: If any read or pagination step is incomplete.
    """
    try:
        branch = _default_branch(repository=repository)
        protected = _digests_at_ref(
            repository=repository,
            ref=branch,
            required=True,
        )
        for sha in _open_pull_heads(repository=repository):
            protected.update(
                _digests_at_ref(repository=repository, ref=sha, required=False),
            )
    except (RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProtectionCollectionError(str(exc)) from exc
    return {digest.lower() for digest in protected}


def collect_default_branch_digests(*, repository: str) -> set[str]:
    """Re-read only the default-branch pin sites before a delete."""
    try:
        branch = _default_branch(repository=repository)
        return {
            digest.lower()
            for digest in _digests_at_ref(
                repository=repository,
                ref=branch,
                required=True,
            )
        }
    except (RuntimeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProtectionCollectionError(str(exc)) from exc


def _pull_request(*, repository: str, number: int) -> tuple[str | None, str | None]:
    """Return a PR's state and merge timestamp.

    A missing pull request yields ``(None, None)`` rather than raising: an
    unknown state never satisfies the closed-unmerged delete rule, so the
    candidate is simply left alone instead of aborting the sweep.
    """
    found, payload = _gh_json_allow_not_found(f"repos/{repository}/pulls/{number}")
    if not found:
        return (None, None)
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
        f"orgs/{owner}/packages/container/{PACKAGE}/versions/{candidate.version_id}"
    )
    found, payload = _gh_json_allow_not_found(endpoint)
    if not found:
        return None
    if not isinstance(payload, dict):
        return None
    return candidate_version(payload)


def _is_protected(*, digest: str, protected: set[str]) -> bool:
    """Return whether *digest* is currently pinned on a protected ref."""
    return bool(digest) and digest.lower() in protected


def _sweep_candidate(
    *,
    candidate: CandidateVersion,
    owner: str,
    repository: str,
    now: datetime,
    min_age_days: int,
    dry_run: bool,
    protected_digests: set[str],
) -> None:
    """Evaluate a single candidate version and delete it when eligible."""
    if not candidate.digest:
        print(
            f"Skipping {candidate.version_id}: version payload has no digest",
        )
        return
    if _is_protected(digest=candidate.digest, protected=protected_digests):
        print(
            f"Skipping {candidate.version_id}: protected (pinned) "
            f"{candidate.digest}",
        )
        return
    pr_states = _pull_request_states(repository=repository, candidate=candidate)
    if not should_delete(
        candidate,
        now=now,
        pr_states=pr_states,
        min_age_days=min_age_days,
    ):
        return
    # Eligibility is evaluated on the initial listing first. Refresh only now,
    # immediately before reporting or deleting, to narrow the TOCTOU window for
    # a newly-added persistent tag.
    refreshed = _refresh_candidate(owner=owner, candidate=candidate)
    if refreshed is None:
        print(
            f"Skipping {candidate.version_id}: package tags changed "
            "or the version was removed",
        )
        return
    try:
        current_protected = collect_default_branch_digests(repository=repository)
        current_protected.update(protected_digests)
    except ProtectionCollectionError as exc:
        raise ProtectionCollectionError(
            f"protection re-check failed for {candidate.version_id}: {exc}",
        ) from exc
    if not refreshed.digest or _is_protected(
        digest=refreshed.digest,
        protected=current_protected,
    ):
        print(
            f"Skipping {candidate.version_id}: protected (pinned) "
            f"{refreshed.digest or 'unknown'}",
        )
        return
    pr_states = _pull_request_states(repository=repository, candidate=refreshed)
    if not should_delete(
        refreshed,
        now=now,
        pr_states=pr_states,
        min_age_days=min_age_days,
    ):
        return
    endpoint = (
        f"orgs/{owner}/packages/container/{PACKAGE}/versions/{refreshed.version_id}"
    )
    if dry_run:
        print(
            f"[dry-run] Would delete {endpoint} (tags: {', '.join(refreshed.tags)})",
        )
        return
    found, _ = _gh_json_allow_not_found("--method", "DELETE", endpoint)
    if not found:
        print(f"Skipping {endpoint}: version was already removed")
        return
    print(f"Deleted {endpoint} (tags: {', '.join(refreshed.tags)})")


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
        versions = _package_versions(owner=owner)
        protected = collect_protected_digests(repository=repository)
    except (RuntimeError, json.JSONDecodeError, ProtectionCollectionError) as exc:
        print(f"Skipping all deletions: {exc}", file=sys.stderr)
        return 1

    candidates = [
        parsed
        for payload in versions
        if (parsed := candidate_version(payload)) is not None
    ]

    failed = False
    for candidate in candidates:
        try:
            _sweep_candidate(
                candidate=candidate,
                owner=owner,
                repository=repository,
                now=now,
                min_age_days=min_age_days,
                dry_run=dry_run,
                protected_digests=protected,
            )
        except ProtectionCollectionError as exc:
            print(f"Skipping remaining deletions: {exc}", file=sys.stderr)
            return 1
        except (RuntimeError, json.JSONDecodeError) as exc:
            # One unreachable version or pull request must not strand every
            # other candidate; report it, keep sweeping, and exit non-zero.
            print(f"Skipping {candidate.version_id}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
