"""Prune untagged GHCR image versions for this repository.

Google-style docstring.

This script lists container package versions for the current repo on GHCR and
deletes those that have no tags AND are older than a retention period.

NOTE: All publish jobs now use ``provenance: false`` and ``sbom: false``,
producing simple Docker v2 manifests with no untagged OCI child manifests.
The min-age guard is retained as defense-in-depth but is no longer the
primary protection mechanism.

Requires GITHUB_TOKEN with packages:write scope in Actions.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
from loguru import logger

# Default minimum age before an untagged version can be deleted (days)
DEFAULT_MIN_AGE_DAYS = 7

# Default minimum age before an ephemeral PR buildcache tag can be deleted (days)
DEFAULT_BUILDCACHE_PR_AGE_DAYS = 14

# Buildcache packages: separate registry repos for BuildKit registry cache exports.
# Distinct retention rules apply (see prune_buildcache_package).
BUILDCACHE_PACKAGES: tuple[str, ...] = (
    "lintro-tools-buildcache",
    "py-lintro-buildcache",
    "py-lintro-base-buildcache",
)

# Permanent buildcache tag preserved across all prune runs.
BUILDCACHE_PERMANENT_TAG = "main"

# Pattern for ephemeral per-PR / per-merge-queue buildcache tags eligible for
# age-based deletion. ``pr-<N>`` is written by pull_request runs;
# ``mq-<run_id>`` is written by merge_group runs (queue attempts can abort, so
# they must not share the permanent ``main`` tag).
EPHEMERAL_TAG_PATTERN = re.compile(r"^(?:pr-\d+|mq-\d+)$")


@dataclass
class GhcrVersion:
    """Container version metadata minimal subset.

    Attributes:
        id: Numeric version id.
        tags: List of tags bound to this version.
        created_at: ISO timestamp when version was created.
        name: The manifest digest/name for this version.
    """

    id: int = field(default=0)
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default="")
    name: str = field(default="")


# Protocols for typed client/response behavior (enables lightweight test doubles).
class _ResponseProto(Protocol):
    headers: Mapping[str, str]
    status_code: int

    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class GhcrClient(Protocol):
    """Protocol for GHCR API client (httpx-compatible)."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = ...,
    ) -> _ResponseProto:
        """Send GET request to URL."""
        ...

    def delete(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = ...,
    ) -> _ResponseProto:
        """Send DELETE request to URL."""
        ...


def get_repo_owner_repo() -> tuple[str, str]:
    """Return (owner, repo) from GITHUB_REPOSITORY env.

    Returns:
        tuple[str, str]: owner and repo.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    owner, name = repo.split("/", 1)
    return owner, name


def _parse_link_header(link_header: str | None) -> str | None:
    """Parse the GitHub Link header to extract the 'next' page URL.

    Args:
        link_header: The Link header value from GitHub API response.

    Returns:
        The URL for the next page, or None if no next page.
    """
    if not link_header:
        return None
    # Link header format: <url>; rel="next", <url>; rel="last"
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            # Extract URL between < and >
            start = part.find("<")
            end = part.find(">")
            if start != -1 and end != -1:
                return part[start + 1 : end]
    return None


def _parse_version_item(item: dict[str, Any]) -> GhcrVersion | None:
    """Parse a single version item from the API response.

    Args:
        item: Dictionary from API response.

    Returns:
        GhcrVersion if successfully parsed, None otherwise.
    """
    vid_raw = item.get("id")
    if vid_raw is None:
        logger.error(
            "API response missing 'id' field for item with created_at: {}",
            item.get("created_at", "unknown"),
        )
        return None
    try:
        vid = int(vid_raw)
    except (ValueError, TypeError) as e:
        logger.error(
            "Invalid 'id' value '{}' for item with created_at: {} - {}",
            vid_raw,
            item.get("created_at", "unknown"),
            e,
        )
        return None
    raw_tags = item.get("metadata", {}).get("container", {}).get("tags")
    tags = list(raw_tags or [])
    created_at = str(item.get("created_at", ""))
    name = str(item.get("name", ""))
    return GhcrVersion(id=vid, tags=tags, created_at=created_at, name=name)


def _get_owner_type(client: GhcrClient, owner: str) -> str:
    """Determine if owner is a user or organization.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner name.

    Returns:
        "Organization" or "User" based on GitHub API response.
    """
    resp = client.get(
        f"https://api.github.com/users/{owner}",
        headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return str(data.get("type", "User"))


def list_container_versions(
    client: GhcrClient,
    owner: str,
    package_name: str = "py-lintro",
    base_path: str | None = None,
) -> list[GhcrVersion]:
    """List container versions for a package (supports both users and orgs).

    Handles pagination to retrieve all versions across multiple pages.
    Uses provided base_path or auto-detects owner type for API endpoint.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).
        package_name: Name of the container package.
        base_path: Pre-computed API base path (avoids redundant owner type lookups).

    Returns:
        list[GhcrVersion]: Version entries.
    """
    versions: list[GhcrVersion] = []

    # Use provided base_path or compute it (fallback for direct calls)
    if base_path is None:
        owner_type = _get_owner_type(client, owner)
        if owner_type == "Organization":
            base_path = f"https://api.github.com/orgs/{owner}/packages/container"
        else:
            base_path = f"https://api.github.com/users/{owner}/packages/container"

    url: str | None = f"{base_path}/{package_name}/versions?per_page=100"

    while url:
        resp = client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data: list[dict[str, Any]] = resp.json()

        for item in data:
            version = _parse_version_item(item)
            if version is not None:
                versions.append(version)

        # Check for next page via Link header
        url = _parse_link_header(resp.headers.get("link"))

    return versions


def parse_iso_datetime(iso_str: str) -> datetime | None:
    """Parse ISO 8601 datetime string to timezone-aware datetime object.

    Args:
        iso_str: ISO format datetime string (e.g., "2026-01-31T20:05:01Z").

    Returns:
        Timezone-aware datetime object in UTC, or None if parsing fails.
    """
    if not iso_str:
        return None
    try:
        # Handle Z suffix and +00:00 formats
        iso_str = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_str)
        # Ensure timezone awareness - attach UTC if naive
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        logger.warning("Failed to parse datetime: {}", iso_str)
        return None


def is_older_than_days(created_at: str, min_age_days: int) -> bool:
    """Check if a version is older than the specified number of days.

    Args:
        created_at: ISO timestamp when version was created.
        min_age_days: Minimum age in days before deletion is allowed.

    Returns:
        True if version is older than min_age_days, False otherwise.
    """
    created = parse_iso_datetime(created_at)
    if created is None:
        # If we can't parse the date, don't delete (be conservative)
        return False
    cutoff = datetime.now(UTC) - timedelta(days=min_age_days)
    return created < cutoff


def delete_version(
    client: GhcrClient,
    owner: str,
    version_id: int,
    package_name: str = "py-lintro",
    base_path: str | None = None,
) -> None:
    """Delete a container version by id.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).
        version_id: GHCR version id to delete.
        package_name: Name of the container package.
        base_path: Pre-computed API base path (avoids redundant owner type lookups).
    """
    # Use provided base_path or compute it (fallback for direct calls)
    if base_path is None:
        owner_type = _get_owner_type(client, owner)
        if owner_type == "Organization":
            base_path = f"https://api.github.com/orgs/{owner}/packages/container"
        else:
            base_path = f"https://api.github.com/users/{owner}/packages/container"

    url = f"{base_path}/{package_name}/versions/{version_id}"
    resp = client.delete(url, headers={"Accept": "application/vnd.github+json"})
    # 204 no content on success
    if resp.status_code not in (204, 404):
        resp.raise_for_status()


def prune_package(
    client: GhcrClient,
    owner: str,
    package_name: str,
    *,
    dry_run: bool,
    min_age_days: int,
    keep_n: int,
) -> int:
    """Prune untagged versions for a single package.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).
        package_name: Name of the container package.
        dry_run: If True, only log what would be deleted.
        min_age_days: Minimum age in days before deletion is allowed.
        keep_n: Keep at least N most recent untagged versions.

    Returns:
        Number of versions deleted (or would be deleted in dry-run).

    Raises:
        httpx.HTTPStatusError: If API request fails (except 404 which is handled).
    """
    logger.info("Processing package: {}", package_name)

    # Compute base_path once to avoid redundant API calls
    owner_type = _get_owner_type(client, owner)
    if owner_type == "Organization":
        base_path = f"https://api.github.com/orgs/{owner}/packages/container"
    else:
        base_path = f"https://api.github.com/users/{owner}/packages/container"

    try:
        versions = list_container_versions(
            client=client,
            owner=owner,
            package_name=package_name,
            base_path=base_path,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning("Package {} not found, skipping", package_name)
            return 0
        raise

    # Filter to untagged versions only
    untagged = [v for v in versions if len(v.tags) == 0]
    logger.info(
        "Found {} total versions, {} untagged for {}",
        len(versions),
        len(untagged),
        package_name,
    )

    # Filter to versions older than min_age_days
    # This protects multi-arch manifest dependencies that are recently created
    old_enough = [v for v in untagged if is_older_than_days(v.created_at, min_age_days)]
    protected_count = len(untagged) - len(old_enough)
    if protected_count > 0:
        logger.info(
            "Protected {} untagged versions younger than {} days",
            protected_count,
            min_age_days,
        )

    # Keep the N most recent untagged by created_at (descending)
    if keep_n > 0:
        # Sort by parsed datetime for accurate ordering (not string comparison)
        old_enough.sort(
            key=lambda v: parse_iso_datetime(v.created_at)
            or datetime.min.replace(
                tzinfo=UTC,
            ),
            reverse=True,
        )
        to_delete = old_enough[keep_n:]
        if len(old_enough) > len(to_delete):
            logger.info(
                "Keeping {} most recent untagged versions per keep_n setting",
                keep_n,
            )
    else:
        to_delete = old_enough

    deleted = 0
    for v in to_delete:
        if dry_run:
            logger.info(
                "[dry-run] Would delete {} version id={} name={} created_at={}",
                package_name,
                v.id,
                v.name[:12] + "..." if len(v.name) > 15 else v.name,
                v.created_at,
            )
        else:
            delete_version(
                client=client,
                owner=owner,
                version_id=v.id,
                package_name=package_name,
                base_path=base_path,
            )
            logger.info(
                "Deleted {} version id={} created_at={}",
                package_name,
                v.id,
                v.created_at,
            )
        deleted += 1

    return deleted


def _resolve_base_path(client: GhcrClient, owner: str) -> str:
    """Return the GHCR API base path for ``owner`` (user vs org).

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).

    Returns:
        Base URL up to ``/packages/container``.
    """
    owner_type = _get_owner_type(client, owner)
    if owner_type == "Organization":
        return f"https://api.github.com/orgs/{owner}/packages/container"
    return f"https://api.github.com/users/{owner}/packages/container"


def _is_pr_only_tagged(version: GhcrVersion) -> bool:
    """Return True if every tag on the version is a ``pr-<N>`` tag.

    A version with mixed tags (e.g. ``["pr-1", "main"]``) is not eligible —
    any non-PR tag protects the version.

    Args:
        version: Version under inspection.

    Returns:
        True when at least one tag is present and all tags match PR pattern.
    """
    if not version.tags:
        return False
    return all(EPHEMERAL_TAG_PATTERN.match(t) for t in version.tags)


def prune_buildcache_package(
    client: GhcrClient,
    owner: str,
    package_name: str,
    *,
    dry_run: bool,
    min_age_days: int,
    pr_age_days: int,
) -> int:
    """Prune a buildcache package using ephemeral-tag-aware retention.

    Rules:
    - Versions tagged ``main`` (or any non-ephemeral tag) are preserved.
    - Versions tagged only with ephemeral tags (``pr-<N>`` from pull_request
      runs or ``mq-<run_id>`` from merge_group runs) are deleted when older
      than ``pr_age_days``.
    - Untagged versions are deleted when older than ``min_age_days``.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).
        package_name: Buildcache package name.
        dry_run: If True, only log what would be deleted.
        min_age_days: Minimum age in days before untagged versions delete.
        pr_age_days: Minimum age in days before ephemeral tags delete.

    Returns:
        Number of versions deleted (or that would be deleted in dry-run).

    Raises:
        httpx.HTTPStatusError: If API request fails (except 404 which is handled).
    """
    logger.info("Processing buildcache package: {}", package_name)

    base_path = _resolve_base_path(client, owner)

    try:
        versions = list_container_versions(
            client=client,
            owner=owner,
            package_name=package_name,
            base_path=base_path,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning("Package {} not found, skipping", package_name)
            return 0
        raise

    pr_tagged_old = [
        v
        for v in versions
        if _is_pr_only_tagged(v) and is_older_than_days(v.created_at, pr_age_days)
    ]
    untagged_old = [
        v
        for v in versions
        if not v.tags and is_older_than_days(v.created_at, min_age_days)
    ]
    to_delete = pr_tagged_old + untagged_old

    logger.info(
        "Buildcache {}: {} total, {} pr-tag eligible, {} untagged eligible",
        package_name,
        len(versions),
        len(pr_tagged_old),
        len(untagged_old),
    )

    deleted = 0
    for v in to_delete:
        if dry_run:
            logger.info(
                "[dry-run] Would delete {} version id={} tags={} created_at={}",
                package_name,
                v.id,
                v.tags,
                v.created_at,
            )
        else:
            delete_version(
                client=client,
                owner=owner,
                version_id=v.id,
                package_name=package_name,
                base_path=base_path,
            )
            logger.info(
                "Deleted {} version id={} tags={} created_at={}",
                package_name,
                v.id,
                v.tags,
                v.created_at,
            )
        deleted += 1

    return deleted


def main() -> int:
    """Entry point.

    Returns:
        int: Process exit code.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN is required")
        return 2

    owner, _ = get_repo_owner_repo()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "py-lintro-ghcr-cleanup",
    }

    dry_run = os.environ.get("GHCR_PRUNE_DRY_RUN", "0") == "1"

    # Minimum age before untagged versions can be deleted (protects multi-arch deps)
    min_age_days_env = os.environ.get(
        "GHCR_PRUNE_MIN_AGE_DAYS",
        str(DEFAULT_MIN_AGE_DAYS),
    )
    try:
        min_age_days = int(min_age_days_env)
    except ValueError:
        min_age_days = DEFAULT_MIN_AGE_DAYS
    if min_age_days < 0:
        min_age_days = DEFAULT_MIN_AGE_DAYS

    # Keep at least N most recent untagged versions
    keep_n_env = os.environ.get("GHCR_PRUNE_KEEP_UNTAGGED_N", "0")
    try:
        keep_n = int(keep_n_env)
    except ValueError:
        keep_n = 0
    if keep_n < 0:
        keep_n = 0

    # Minimum age for ephemeral pr-<N> buildcache tags
    pr_age_env = os.environ.get(
        "GHCR_PRUNE_BUILDCACHE_PR_AGE_DAYS",
        str(DEFAULT_BUILDCACHE_PR_AGE_DAYS),
    )
    try:
        pr_age_days = int(pr_age_env)
    except ValueError:
        pr_age_days = DEFAULT_BUILDCACHE_PR_AGE_DAYS
    if pr_age_days < 0:
        pr_age_days = DEFAULT_BUILDCACHE_PR_AGE_DAYS

    # Production image packages — delete untagged only.
    packages = ["py-lintro", "lintro-tools"]

    logger.info(
        "GHCR cleanup starting (dry_run={}, min_age_days={}, keep_n={}, "
        "buildcache_pr_age_days={})",
        dry_run,
        min_age_days,
        keep_n,
        pr_age_days,
    )

    total_deleted = 0
    with httpx.Client(headers=headers, timeout=30) as client:
        # Cast httpx.Client to GhcrClient - they are compatible at runtime
        # but mypy can't verify this due to httpx's complex method signatures
        typed_client = cast(GhcrClient, client)
        for package_name in packages:
            deleted = prune_package(
                client=typed_client,
                owner=owner,
                package_name=package_name,
                dry_run=dry_run,
                min_age_days=min_age_days,
                keep_n=keep_n,
            )
            total_deleted += deleted
        for package_name in BUILDCACHE_PACKAGES:
            deleted = prune_buildcache_package(
                client=typed_client,
                owner=owner,
                package_name=package_name,
                dry_run=dry_run,
                min_age_days=min_age_days,
                pr_age_days=pr_age_days,
            )
            total_deleted += deleted

    action = "Would delete" if dry_run else "Deleted"
    logger.info("{} {} untagged GHCR versions total", action, total_deleted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
