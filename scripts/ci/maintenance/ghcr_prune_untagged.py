"""Compatibility entry point for ``ghcr_prune_untagged``.

The implementation lives in :mod:`scripts.ci.maintenance.ghcr_prune`. This
module exists so the workflow invocation
``uv run python scripts/ci/maintenance/ghcr_prune_untagged.py`` keeps working,
and so existing imports of the public names from this path continue to
resolve.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When invoked as ``python scripts/ci/maintenance/ghcr_prune_untagged.py``
# (no ``-m``), Python sets ``sys.path[0]`` to this file's directory and the
# top-level ``scripts`` package is unreachable. Insert the repo root so the
# absolute import below resolves.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ci.maintenance.ghcr_prune import (  # noqa: E402
    BUILDCACHE_PACKAGES,
    BUILDCACHE_PERMANENT_TAG,
    DEFAULT_BUILDCACHE_PR_AGE_DAYS,
    DEFAULT_MIN_AGE_DAYS,
    EPHEMERAL_TAG_PATTERN,
    GhcrClient,
    GhcrVersion,
    ProtectionResult,
    RegistryFetchError,
    collect_referenced_digests,
    delete_version,
    fetch_manifest,
    fetch_referrers,
    get_repo_owner_repo,
    is_ephemeral_only_tagged,
    is_older_than_days,
    list_container_versions,
    main,
    parse_iso_datetime,
    prune_buildcache_package,
    prune_package,
    resolve_base_path,
)

__all__ = [
    "BUILDCACHE_PACKAGES",
    "BUILDCACHE_PERMANENT_TAG",
    "DEFAULT_BUILDCACHE_PR_AGE_DAYS",
    "DEFAULT_MIN_AGE_DAYS",
    "EPHEMERAL_TAG_PATTERN",
    "GhcrClient",
    "GhcrVersion",
    "ProtectionResult",
    "RegistryFetchError",
    "collect_referenced_digests",
    "delete_version",
    "fetch_manifest",
    "fetch_referrers",
    "get_repo_owner_repo",
    "is_ephemeral_only_tagged",
    "is_older_than_days",
    "list_container_versions",
    "main",
    "parse_iso_datetime",
    "prune_buildcache_package",
    "prune_package",
    "resolve_base_path",
]


if __name__ == "__main__":
    raise SystemExit(main())
