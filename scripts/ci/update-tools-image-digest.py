#!/usr/bin/env python3
"""Update the two ``lintro-tools`` digest pins in the repository.

The root Dockerfile and ``docker/ai-tools.Dockerfile`` deliberately consume the
same immutable tools image.  This script is the single writer for those pins;
the candidate workflow can therefore use its ``changed`` output as an
idempotence gate before minting the write-scoped GitHub App token.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_FILES = (
    REPO_ROOT / "Dockerfile",
    REPO_ROOT / "docker" / "ai-tools.Dockerfile",
)
IMAGE_REF = "ghcr.io/lgtm-hq/lintro-tools:latest@"
DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
PIN_RE = re.compile(
    rf"(?P<prefix>{re.escape(IMAGE_REF)})sha256:[a-f0-9]{{64}}",
)


def update_digest(*, digest: str, paths: tuple[Path, ...] = PIN_FILES) -> bool:
    """Replace every tools-image pin with *digest*.

    Args:
        digest: Full ``sha256:<64 hex characters>`` digest.
        paths: Dockerfiles containing the canonical pin sites.

    Returns:
        Whether at least one file changed.

    Raises:
        ValueError: If the digest or an expected pin site is invalid.
    """
    if DIGEST_RE.fullmatch(digest) is None:
        raise ValueError(f"invalid image digest: {digest!r}")

    contents: list[tuple[Path, str, str]] = []
    for path in paths:
        original = path.read_text(encoding="utf-8")
        matches = list(PIN_RE.finditer(original))
        if len(matches) != 1:
            raise ValueError(
                f"{path}: expected exactly one digest-pinned {IMAGE_REF} reference, "
                f"found {len(matches)}",
            )
        updated = PIN_RE.sub(rf"\g<prefix>{digest}", original)
        contents.append((path, original, updated))

    changed = any(original != updated for _, original, updated in contents)
    for path, _, updated in contents:
        if path.read_text(encoding="utf-8") != updated:
            path.write_text(updated, encoding="utf-8")
    return changed


def _write_output(*, changed: bool) -> None:
    """Write the workflow output when running under GitHub Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")


def main(*, argv: list[str] | None = None) -> int:
    """Run the digest updater CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest", required=True, help="sha256:<64 hex characters>")
    args = parser.parse_args(argv)

    try:
        changed = update_digest(digest=args.digest)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _write_output(changed=changed)
    state = "updated" if changed else "already matches"
    print(f"lintro-tools digest {state}: {args.digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
