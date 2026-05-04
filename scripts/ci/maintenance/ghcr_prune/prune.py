"""Per-package prune logic.

Two entry points:

- :func:`prune_package` — production image packages (delete untagged only,
  honour ``referenced_digests``, ``keep_n``, ``min_age_days``).
- :func:`prune_buildcache_package` — buildcache repos (also reaps
  ephemeral ``pr-<N>`` / ``mq-<run>`` / ``dispatch-<run>`` tags).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from loguru import logger

from .api import (
    delete_version,
    list_container_versions,
    resolve_base_path,
)
from .dates import is_older_than_days, parse_iso_datetime
from .protocols import GhcrClient
from .tags import is_ephemeral_only_tagged
from .version import GhcrVersion


def prune_package(
    client: GhcrClient,
    owner: str,
    package_name: str,
    *,
    dry_run: bool,
    min_age_days: int,
    keep_n: int,
    referenced_digests: set[str] | None = None,
    versions: list[GhcrVersion] | None = None,
) -> int:
    """Prune untagged versions for a single production package.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).
        package_name: Name of the container package.
        dry_run: If True, only log what would be deleted.
        min_age_days: Minimum age in days before deletion is allowed.
        keep_n: Keep at least N most recent untagged versions.
        referenced_digests: Digests referenced by tagged manifests
            (multi-arch children, SLSA / cosign attestations). Versions whose
            ``name`` is in this set are protected.
        versions: Pre-fetched version list. When provided the function skips
            the GitHub API call and reuses the caller's list.

    Returns:
        Number of versions deleted (or that would be in dry-run).

    Raises:
        httpx.HTTPStatusError: On non-404 GitHub API errors.
    """
    referenced = referenced_digests or set()
    logger.info("Processing package: {}", package_name)

    base_path = resolve_base_path(client=client, owner=owner)
    if versions is None:
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

    untagged = [v for v in versions if len(v.tags) == 0]
    logger.info(
        "Found {} total versions, {} untagged for {}",
        len(versions),
        len(untagged),
        package_name,
    )

    old_enough = [v for v in untagged if is_older_than_days(v.created_at, min_age_days)]
    protected_count = len(untagged) - len(old_enough)
    if protected_count > 0:
        logger.info(
            "Protected {} untagged versions younger than {} days",
            protected_count,
            min_age_days,
        )

    if referenced:
        before = len(old_enough)
        old_enough = [v for v in old_enough if v.name not in referenced]
        protected_refs = before - len(old_enough)
        if protected_refs > 0:
            logger.info(
                "Protected {} untagged versions referenced by tagged manifests",
                protected_refs,
            )

    if keep_n > 0:
        old_enough.sort(
            key=lambda v: parse_iso_datetime(iso_str=v.created_at)
            or datetime.min.replace(tzinfo=UTC),
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
    - Versions tagged only with ephemeral tags (``pr-<N>``, ``mq-<run_id>``,
      ``dispatch-<run_id>``) are deleted when older than ``pr_age_days``.
    - Untagged versions are deleted when older than ``min_age_days``.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner (user/org).
        package_name: Buildcache package name.
        dry_run: If True, only log what would be deleted.
        min_age_days: Minimum age in days before untagged versions delete.
        pr_age_days: Minimum age in days before ephemeral tags delete.

    Returns:
        Number of versions deleted (or that would be in dry-run).

    Raises:
        httpx.HTTPStatusError: On non-404 GitHub API errors.
    """
    logger.info("Processing buildcache package: {}", package_name)

    base_path = resolve_base_path(client=client, owner=owner)

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
        if is_ephemeral_only_tagged(version=v)
        and is_older_than_days(v.created_at, pr_age_days)
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
