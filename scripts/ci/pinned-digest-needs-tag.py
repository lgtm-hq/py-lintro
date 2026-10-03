#!/usr/bin/env python3
"""Exit 0 when a lintro-tools digest still needs a persistent GHCR tag.

Used by the one-time backfill (#2845). A digest whose only tags are
ephemeral (``tools-candidate-*``, ``sha-*``, ``renovate-*``) is the
shape that the sweeper deletes. Any other tag, including ``pinned-*``
or ``latest``, is enough to keep it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

try:
    from github_api import gh_json as _gh_json
except ModuleNotFoundError:
    from scripts.ci.github_api import gh_json as _gh_json

PACKAGE = "lintro-tools"
DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
EPHEMERAL_RE = re.compile(
    r"^(?:tools-candidate-pr[1-9][0-9]*-[0-9a-f]{7,40}|sha-|renovate-)",
)
PAGE_SIZE = 100


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


def _version_tags(*, owner: str, digest: str) -> tuple[str, ...] | None:
    """Return tags on *digest*, or ``None`` if the version is missing."""
    payload = _gh_json(
        f"orgs/{owner}/packages/container/{PACKAGE}/versions?per_page={PAGE_SIZE}",
        "--paginate",
        "--slurp",
    )
    for version in _pages(payload):
        if version.get("name") != digest:
            continue
        metadata = version.get("metadata")
        container = metadata.get("container") if isinstance(metadata, dict) else None
        tags = container.get("tags") if isinstance(container, dict) else None
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise RuntimeError(f"malformed tags for {digest}")
        return tuple(tags)
    return None


def digest_needs_persistent_tag(*, owner: str, digest: str) -> bool:
    """Return whether *digest* has only ephemeral tags, or is unknown.

    Args:
        owner: GHCR org that owns ``lintro-tools``.
        digest: Full ``sha256:<64 hex>`` digest.

    Returns:
        ``True`` when the digest is missing or every tag is ephemeral.

    Raises:
        RuntimeError: If the package listing cannot be parsed.
    """
    tags = _version_tags(owner=owner, digest=digest)
    if tags is None:
        return True
    return all(EPHEMERAL_RE.match(tag) for tag in tags)


def main() -> int:
    """Print whether ``DIGEST`` still needs a persistent tag."""
    digest = os.environ.get("DIGEST", "").strip()
    if DIGEST_RE.fullmatch(digest) is None:
        print("DIGEST must be sha256:<64 hex characters>", file=sys.stderr)
        return 2
    repository = os.environ.get("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    owner = repository.split("/", 1)[0]
    try:
        needs_tag = digest_needs_persistent_tag(owner=owner, digest=digest)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if needs_tag:
        print(f"{digest} has no persistent tag")
        return 0
    print(f"{digest} already has a persistent tag")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
