#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Point the pinned py-lintro release image at the newest published release.

The nightly dogfood run and the docker-ci fork-PR fallback pin a released
``py-lintro`` image by tag and digest. The pin is what makes the digest-lag
gate in ``verify-image-manifest-tools.sh`` meaningful, but nothing moved it
automatically: it drifted from 0.80.3 to eleven minor versions behind before
anyone noticed (#1590).

Renovate cannot do it. The ``docker`` datasource has to enumerate tags to find
a newer version, and ``ghcr.io/lgtm-hq/py-lintro`` carries thousands — the
first 1000-tag page contains neither the current pin nor any recent release,
because over half of it is ``sha-<commit>`` CI tags. So the pin is synced here
instead, from the release Version-PR finalizer, where the answer is a direct
lookup rather than a search.

**The pin deliberately lags by one release.** This runs while the Version-PR
for release *X* is being prepared, and the image for *X* does not exist yet —
it is built after the tag. So the pin targets the newest release already
published, *X-1*. That is a bounded, self-healing lag: a manifest tool bump
landing in *X* shows as digest lag until *X+1*, instead of drifting
indefinitely.

**Failures are non-fatal.** This runs in the unattended release path, so an
unreachable API, a missing permission, or an unparseable payload must never
break the release PR. Every failure logs a GitHub ``::warning::`` and leaves
the pin untouched — visible in the run, but never blocking a release.

Digests are resolved through the GitHub Packages API on ``api.github.com``
rather than the registry: the Version-PR job's egress allowlist permits the
former and not ``ghcr.io``. Standard library only, for the same reason.

Usage:
    python3 scripts/ci/sync-pinned-release-image.py
    python3 scripts/ci/sync-pinned-release-image.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Every workflow carrying a pinned reference, and how many sites it must hold.
# Counts are asserted so a partial rewrite is a failure rather than a silent
# half-update — the same contract tests/unit/test_workflow_wiring.py enforces.
PINNED_SITES: dict[str, int] = {
    ".github/workflows/dogfood-nightly.yml": 3,
    ".github/workflows/docker-ci.yml": 4,
}

ORG = "lgtm-hq"
PACKAGE = "py-lintro"

# The package carries thousands of ephemeral versions, so the newest release
# is not guaranteed to sit on page one. Walk pages until a release tag is
# found, bounded so a pathological listing cannot spin (#1590).
_PER_PAGE = 100
_MAX_PAGES = 20

_PIN_PATTERN = re.compile(
    r"ghcr\.io/lgtm-hq/py-lintro:(?P<version>\d+\.\d+\.\d+)"
    r"@(?P<digest>sha256:[a-f0-9]{64})",
)
_RELEASE_TAG = re.compile(r"^\d+\.\d+\.\d+$")


def _warn(message: str) -> None:
    """Emit a GitHub Actions warning annotation.

    Args:
        message: Human-readable explanation of what was skipped and why.
    """
    print(f"::warning::sync-pinned-release-image: {message}", file=sys.stderr)


def _version_key(version: str) -> tuple[int, ...]:
    """Return a sortable key for a ``major.minor.patch`` string.

    Args:
        version: Release version without a leading ``v``.

    Returns:
        Numeric components, comparable across releases.
    """
    return tuple(int(part) for part in version.split("."))


def _fetch_package_versions(token: str) -> list[dict[str, Any]]:
    """Fetch container versions for the release package.

    Args:
        token: GitHub token with ``read:packages``.

    Returns:
        Parsed version records, or an empty list when unavailable.
    """
    versions: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        page_records = _fetch_page(token=token, page=page)
        if not page_records:
            break
        versions.extend(page_records)
        if len(page_records) < _PER_PAGE:
            break  # short page means the last one
        if latest_published_release(versions) is not None:
            # A release tag is in hand; deeper pages are older by construction
            # (the API returns newest first), so stop rather than walk them all.
            break
    else:
        _warn(
            f"stopped after {_MAX_PAGES} pages of package versions; "
            "pin may be based on an incomplete list",
        )
    return versions


def _fetch_page(*, token: str, page: int) -> list[dict[str, Any]]:
    """Fetch one page of container versions.

    The package carries thousands of ``sha-``/``ci-`` versions, so a single
    page is not enough to guarantee a release tag is visible — the exact
    enumeration problem that stopped Renovate maintaining this pin (#1590).

    Args:
        token: GitHub token with ``read:packages``.
        page: 1-based page number.

    Returns:
        Version records for the page, or an empty list when unavailable.
    """
    url = (
        f"https://api.github.com/orgs/{ORG}/packages/container/"
        f"{PACKAGE}/versions?per_page={_PER_PAGE}&page={page}"
    )
    if not url.startswith("https://"):  # pragma: no cover - constant URL
        _warn("refusing to fetch a non-HTTPS URL; pin unchanged")
        return []
    # The https:// scheme is asserted above, so no file:/custom scheme can be
    # opened; the URL is built from module constants, not from user input.
    request = (
        urllib.request.Request(  # noqa: S310 — HTTPS-only validated above  # nosec B310
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "py-lintro-pin-sync",
            },
        )
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected  # nosec B310
            request,
            timeout=30,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 403 is the expected shape when the release token lacks read:packages.
        _warn(f"packages API returned HTTP {exc.code}; pin left unchanged")
        return []
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _warn(f"packages API unreachable ({type(exc).__name__}); pin unchanged")
        return []

    return payload if isinstance(payload, list) else []


def latest_published_release(versions: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Return the newest published ``(version, digest)`` pair.

    Only versions carrying a bare ``major.minor.patch`` tag are considered, so
    ``sha-<commit>``, ``ci-<run_id>``, ``latest`` and ``cache`` are ignored.

    Args:
        versions: Records from the GitHub Packages API.

    Returns:
        The newest release and its digest, or None when none qualify.
    """
    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for record in versions:
        if not isinstance(record, dict):
            continue
        digest = str(record.get("name", ""))
        if not digest.startswith("sha256:"):
            continue
        metadata = record.get("metadata")
        container = metadata.get("container") if isinstance(metadata, dict) else None
        tags = container.get("tags") if isinstance(container, dict) else None
        if not isinstance(tags, list):
            continue
        for tag in tags:
            tag_text = str(tag)
            if _RELEASE_TAG.match(tag_text):
                candidates.append((_version_key(tag_text), tag_text, digest))

    if not candidates:
        return None
    _, version, digest = max(candidates)
    return version, digest


def apply_pin(*, version: str, digest: str, dry_run: bool = False) -> bool:
    """Rewrite every pinned reference to ``version``/``digest``.

    Args:
        version: Release version to pin.
        digest: Image digest for that release.
        dry_run: When True, report what would change without writing.

    Returns:
        True when a change was made (or would be), False when already current.
    """
    replacement = f"ghcr.io/lgtm-hq/py-lintro:{version}@{digest}"

    # Two phases on purpose. Validating and writing in one pass would rewrite
    # dogfood-nightly.yml and then bail on docker-ci.yml, leaving the workflows
    # pinned to different images — the exact partial bump the wiring test in
    # #1752 exists to catch, reached here without any failure being reported.
    planned: list[tuple[Path, str]] = []
    for relative_path, expected_sites in PINNED_SITES.items():
        path = _REPO_ROOT / relative_path
        if not path.is_file():
            _warn(f"{relative_path} not found; pin unchanged")
            return False

        text = path.read_text(encoding="utf-8")
        matches = _PIN_PATTERN.findall(text)
        if len(matches) != expected_sites:
            # A changed pin shape means this script no longer understands the
            # file, so no file is touched.
            _warn(
                f"{relative_path}: expected {expected_sites} pinned sites, "
                f"found {len(matches)}; pin unchanged",
            )
            return False

        planned.append((path, _PIN_PATTERN.sub(replacement, text)))

    changed = False
    for path, updated in planned:
        if path.read_text(encoding="utf-8") == updated:
            continue
        changed = True
        if not dry_run:
            path.write_text(updated, encoding="utf-8")

    return changed


def main(argv: list[str] | None = None) -> int:
    """Sync the pinned release image, never failing the caller.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        Always 0 — this runs in the unattended release path, so a problem
        here must warn rather than block a release.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the change without writing files",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        _warn("no GH_TOKEN/GITHUB_TOKEN in environment; pin unchanged")
        return 0

    resolved = latest_published_release(_fetch_package_versions(token))
    if resolved is None:
        _warn("no published release version found in the package; pin unchanged")
        return 0

    version, digest = resolved
    if apply_pin(version=version, digest=digest, dry_run=args.dry_run):
        action = "would pin" if args.dry_run else "pinned"
        print(f"[INFO] {action} py-lintro release image to {version} ({digest})")
    else:
        print(f"[INFO] pinned release image already at {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
