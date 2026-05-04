"""Command-line entry point for the GHCR prune utility.

Reads configuration from environment variables, orchestrates the
per-package walk, and skips pruning when SLSA / cosign protection cannot
be computed completely (registry auth failure or transient errors).

Environment variables:

- ``GITHUB_TOKEN`` (required) — token with ``packages:write``.
- ``GHCR_PRUNE_DRY_RUN`` — ``1`` to log only.
- ``GHCR_PRUNE_MIN_AGE_DAYS`` — defaults to 7.
- ``GHCR_PRUNE_KEEP_UNTAGGED_N`` — keep N most recent untagged. Defaults to 0.
- ``GHCR_PRUNE_BUILDCACHE_PR_AGE_DAYS`` — defaults to 14.
- ``GHCR_PRUNE_PROTECT_REFERENCED`` — ``0`` to disable referenced-digest
  protection. Defaults to ``1``.
"""

from __future__ import annotations

import os
from typing import cast

import httpx
from loguru import logger

from .api import (
    get_repo_owner_repo,
    list_container_versions,
    resolve_base_path,
)
from .protocols import GhcrClient
from .prune import prune_buildcache_package, prune_package
from .registry import (
    _exchange_registry_token,
    collect_referenced_digests,
)
from .tags import BUILDCACHE_PACKAGES
from .version import GhcrVersion

# Default minimum age before an untagged version can be deleted (days)
DEFAULT_MIN_AGE_DAYS = 7

# Default minimum age before an ephemeral PR buildcache tag can be deleted
DEFAULT_BUILDCACHE_PR_AGE_DAYS = 14

# Production image packages — referenced-digest protection applies here.
_PROD_PACKAGES: tuple[str, ...] = ("py-lintro", "lintro-tools")


def _read_int_env(name: str, default: int) -> int:
    """Return a non-negative int from env ``name`` or ``default``.

    Args:
        name: Environment variable name.
        default: Value to return when the env var is missing or invalid.

    Returns:
        Parsed integer or the default.
    """
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return default if value < 0 else value


def main() -> int:
    """Entry point.

    Returns:
        Process exit code.
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
    min_age_days = _read_int_env(
        name="GHCR_PRUNE_MIN_AGE_DAYS",
        default=DEFAULT_MIN_AGE_DAYS,
    )
    keep_n = _read_int_env(
        name="GHCR_PRUNE_KEEP_UNTAGGED_N",
        default=0,
    )
    pr_age_days = _read_int_env(
        name="GHCR_PRUNE_BUILDCACHE_PR_AGE_DAYS",
        default=DEFAULT_BUILDCACHE_PR_AGE_DAYS,
    )
    protect_referenced = os.environ.get("GHCR_PRUNE_PROTECT_REFERENCED", "1") == "1"

    logger.info(
        "GHCR cleanup starting (dry_run={}, min_age_days={}, keep_n={}, "
        "buildcache_pr_age_days={}, protect_referenced={})",
        dry_run,
        min_age_days,
        keep_n,
        pr_age_days,
        protect_referenced,
    )

    total_deleted = 0
    with httpx.Client(headers=headers, timeout=30) as client:
        # Cast httpx.Client to GhcrClient — compatible at runtime, mypy can't
        # verify due to httpx's complex method signatures.
        typed_client = cast(GhcrClient, client)
        for package_name in _PROD_PACKAGES:
            total_deleted += _prune_one_prod_package(
                client=typed_client,
                owner=owner,
                package_name=package_name,
                token=token,
                dry_run=dry_run,
                min_age_days=min_age_days,
                keep_n=keep_n,
                protect_referenced=protect_referenced,
            )
        for package_name in BUILDCACHE_PACKAGES:
            total_deleted += prune_buildcache_package(
                client=typed_client,
                owner=owner,
                package_name=package_name,
                dry_run=dry_run,
                min_age_days=min_age_days,
                pr_age_days=pr_age_days,
            )

    action = "Would delete" if dry_run else "Deleted"
    logger.info(
        "{} {} GHCR versions total (untagged + ephemeral pr-*/mq-*/dispatch-* "
        "buildcache; referenced digests {})",
        action,
        total_deleted,
        "preserved" if protect_referenced else "NOT preserved",
    )
    return 0


def _prune_one_prod_package(
    client: GhcrClient,
    owner: str,
    package_name: str,
    token: str,
    *,
    dry_run: bool,
    min_age_days: int,
    keep_n: int,
    protect_referenced: bool,
) -> int:
    """Prefetch + protect + prune one production package.

    Encapsulates the registry-walk-then-prune flow so :func:`main` stays
    short. Skips pruning entirely when registry auth fails or the
    protection set is incomplete (transient registry errors).

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner.
        package_name: Container package name.
        token: GitHub token (forwarded to the registry token exchange).
        dry_run: Forwarded to :func:`prune_package`.
        min_age_days: Forwarded.
        keep_n: Forwarded.
        protect_referenced: When False, skips the registry walk entirely.

    Returns:
        Number of versions deleted (0 when the package is skipped).

    Raises:
        httpx.HTTPStatusError: On non-404 GitHub API errors during the
            reference-protection prefetch.
    """
    referenced: set[str] = set()
    prefetched: list[GhcrVersion] | None = None
    if protect_referenced:
        base_path = resolve_base_path(client=client, owner=owner)
        try:
            prefetched = list_container_versions(
                client=client,
                owner=owner,
                package_name=package_name,
                base_path=base_path,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            logger.warning("Package {} not found, skipping", package_name)
            return 0
        if prefetched:
            reg_token = _exchange_registry_token(
                client=client,
                owner=owner,
                package_name=package_name,
                github_token=token,
            )
            if reg_token is None:
                logger.warning(
                    "Skipping prune for {} (registry auth failed; "
                    "cannot compute reference protection)",
                    package_name,
                )
                return 0
            result = collect_referenced_digests(
                client=client,
                owner=owner,
                package_name=package_name,
                versions=prefetched,
                registry_token=reg_token,
            )
            if not result.complete:
                logger.warning(
                    "Skipping prune for {} (referenced-digest collection "
                    "incomplete; transient registry error)",
                    package_name,
                )
                return 0
            referenced = result.digests
            logger.info(
                "Collected {} referenced digests for {}",
                len(referenced),
                package_name,
            )
    return prune_package(
        client=client,
        owner=owner,
        package_name=package_name,
        dry_run=dry_run,
        min_age_days=min_age_days,
        keep_n=keep_n,
        referenced_digests=referenced,
        versions=prefetched,
    )
