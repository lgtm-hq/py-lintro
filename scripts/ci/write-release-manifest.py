#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Write the release manifest for a tag run (#2562).

The staging Docker build exports the three multi-arch index digests as job
outputs; this script records them, keyed by image, in ``release-manifest.json``
so the promote job, a publish rerun and the recovery window all read the same
digests the build stage attested. PR (c) of #2562 extends the file with the
dist and binary entries; the ``images`` key shape is stable.

Usage:
    RELEASE_TAG=<tag> RUN_ID=<id> BASE_DIGEST=<sha256:...> \
        FULL_DIGEST=<sha256:...> AI_DIGEST=<sha256:...> \
        OUTPUT=release-manifest.json python3 scripts/ci/write-release-manifest.py

Exit codes:
    0 — Manifest written.
    1 — A required variable is missing or a digest is malformed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Environment variable -> image the digest belongs to.
IMAGE_DIGEST_VARS: dict[str, str] = {
    "BASE_DIGEST": "ghcr.io/lgtm-hq/py-lintro-base",
    "FULL_DIGEST": "ghcr.io/lgtm-hq/py-lintro",
    "AI_DIGEST": "ghcr.io/lgtm-hq/py-lintro-ai",
}

SCHEMA_VERSION = 1


def build_manifest(env: dict[str, str]) -> dict[str, object]:
    """Assemble the manifest document from the environment.

    Args:
        env: Environment mapping (normally ``os.environ``).

    Returns:
        The manifest document.

    Raises:
        ValueError: A required variable is missing or a digest is malformed.
    """
    tag = env.get("RELEASE_TAG", "").strip()
    run_id = env.get("RUN_ID", "").strip()
    if not tag:
        raise ValueError("RELEASE_TAG is required")
    if not run_id:
        raise ValueError("RUN_ID is required")
    images: dict[str, str] = {}
    for var, image in IMAGE_DIGEST_VARS.items():
        digest = env.get(var, "").strip()
        if not digest:
            raise ValueError(f"{var} is required (no digest for {image})")
        if not DIGEST_RE.match(digest):
            raise ValueError(f"{var} is not a sha256 digest: {digest!r}")
        images[image] = digest
    return {
        "schema": SCHEMA_VERSION,
        "tag": tag,
        "run_id": run_id,
        "images": images,
    }


def main() -> int:
    """Write the manifest named by ``OUTPUT``.

    Returns:
        Process exit code.
    """
    output = os.environ.get("OUTPUT", "").strip()
    if not output:
        print("OUTPUT is required", file=sys.stderr)
        return 1
    try:
        manifest = build_manifest(dict(os.environ))
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    path = Path(output)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path} with {len(IMAGE_DIGEST_VARS)} image digest(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
