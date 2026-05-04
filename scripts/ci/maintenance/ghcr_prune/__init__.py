"""GHCR untagged-version prune package.

Public surface re-exported here so callers (workflows, tests) can use a
single import path. The thin shim
``scripts/ci/maintenance/ghcr_prune_untagged.py`` preserves the historical
file-based invocation.
"""

from __future__ import annotations

from .api import (
    delete_version,
    get_repo_owner_repo,
    list_container_versions,
    resolve_base_path,
)
from .cli import (
    DEFAULT_BUILDCACHE_PR_AGE_DAYS,
    DEFAULT_MIN_AGE_DAYS,
    main,
)
from .dates import is_older_than_days, parse_iso_datetime
from .protocols import GhcrClient
from .prune import prune_buildcache_package, prune_package
from .registry import (
    ProtectionResult,
    RegistryFetchError,
    collect_referenced_digests,
    fetch_manifest,
    fetch_referrers,
)
from .tags import (
    BUILDCACHE_PACKAGES,
    BUILDCACHE_PERMANENT_TAG,
    EPHEMERAL_TAG_PATTERN,
    is_ephemeral_only_tagged,
)
from .version import GhcrVersion

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
