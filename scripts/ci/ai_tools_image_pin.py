#!/usr/bin/env python3
"""Resolve the digest-pinned lintro-ai-tools image from the root Dockerfile.

The AI CLI contract tests run inside the same ``lintro-ai-tools`` image the
released ``ai`` variant is built from, so the agent-CLI versions they verify are
exactly the ones lintro ships. Reading the pin out of the Dockerfile — instead of
copying the digest into the workflow — keeps a single Renovate-managed pin site.
A second copy would drift, and a contract gate verifying a *different* image than
the one users get is worse than no gate at all.

Stdlib only and no lintro import: this runs on a bare CI runner before any
dependency install.

Usage:
    scripts/ci/ai_tools_image_pin.py [--dockerfile Dockerfile] [--stage aitools]

Prints the fully qualified image reference (tag plus digest) on stdout.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_DOCKERFILE = "Dockerfile"
DEFAULT_STAGE = "aitools"


def build_stage_pattern(*, stage: str) -> re.Pattern[str]:
    """Build the regex matching a ``FROM <ref> AS <stage>`` line.

    Args:
        stage: Build-stage name to match, case-insensitively.

    Returns:
        A compiled pattern whose first group is the image reference.
    """
    return re.compile(
        rf"^\s*FROM\s+(\S+)\s+AS\s+{re.escape(stage)}\s*$",
        re.IGNORECASE,
    )


def resolve_image(*, dockerfile_text: str, stage: str) -> str:
    """Extract the digest-pinned image reference for a build stage.

    Args:
        dockerfile_text: Full Dockerfile contents.
        stage: Build-stage name whose base image is wanted.

    Returns:
        The image reference exactly as pinned in the Dockerfile.

    Raises:
        ValueError: When the stage is absent, or its base image carries no digest
            — an unpinned base would silently change what the gate verifies.
    """
    pattern = build_stage_pattern(stage=stage)
    for line in dockerfile_text.splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        image = match.group(1)
        if "@sha256:" not in image:
            msg = (
                f"stage '{stage}' base image is not digest-pinned: {image}. "
                "The contract gate must verify an immutable image."
            )
            raise ValueError(msg)
        return image

    msg = f"no `FROM ... AS {stage}` stage found in the Dockerfile"
    raise ValueError(msg)


def main(*, argv: list[str] | None = None) -> int:
    """Print the pinned image reference for the requested stage.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dockerfile",
        default=DEFAULT_DOCKERFILE,
        help="Path to the Dockerfile holding the pin.",
    )
    parser.add_argument(
        "--stage",
        default=DEFAULT_STAGE,
        help="Build stage whose base image should be resolved.",
    )
    args = parser.parse_args(argv)

    path = Path(args.dockerfile)
    if not path.exists():
        print(f"Dockerfile not found: {path}", file=sys.stderr)
        return 1

    try:
        image = resolve_image(
            dockerfile_text=path.read_text(encoding="utf-8"),
            stage=args.stage,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
