"""Buildcache tag constants and ephemeral-tag detection."""

from __future__ import annotations

import re

from .version import GhcrVersion

# BuildKit registry cache lives on production packages as ``:cache`` (and
# platform-specific ``cache-*`` tags). Ephemeral ``pr-*`` / ``mq-*`` /
# ``dispatch-*`` cache exports share those packages — see
# :func:`prune_buildcache_package` with ``prune_untagged=False``.
BUILDCACHE_PACKAGES: tuple[str, ...] = ()

# Permanent cache tag preserved across all prune runs (also protects ``main``,
# semver, ``latest``, and other non-ephemeral tags via mixed-tag rules).
BUILDCACHE_PERMANENT_TAG = "cache"

# Pattern for ephemeral buildcache tags eligible for age-based deletion:
#   pr-<N>          — pull_request runs
#   mq-<run_id>     — merge_group runs (queue attempts can abort)
#   dispatch-<run>  — workflow_dispatch on a non-main branch (feature dry-runs)
# Each must not share the permanent ``main`` tag because they can carry code
# that never reaches main.
EPHEMERAL_TAG_PATTERN = re.compile(r"^(?:pr-\d+|mq-\d+|dispatch-\d+)$")


def is_ephemeral_only_tagged(version: GhcrVersion) -> bool:
    """Return True if every tag on ``version`` matches the ephemeral pattern.

    Mixed tags (e.g. ``["pr-1", "main"]``) are not eligible — any non-ephemeral
    tag protects the version.

    Args:
        version: Version under inspection.

    Returns:
        True when at least one tag is present and all tags match the
        ephemeral pattern.
    """
    if not version.tags:
        return False
    return all(EPHEMERAL_TAG_PATTERN.match(t) for t in version.tags)
