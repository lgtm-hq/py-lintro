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
    rf"(?P<prefix>{re.escape(IMAGE_REF)})(?P<digest>sha256:[a-f0-9]{{64}})(?=\s|$)",
)
PINNED_TAG_PREFIX = "pinned-"


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


def read_pin_digest(*, paths: tuple[Path, ...] = PIN_FILES) -> str:
    """Return the ``lintro-tools`` digest both pin sites agree on.

    Args:
        paths: Dockerfiles that must each contain exactly one canonical pin.

    Returns:
        The shared ``sha256:<64 hex characters>`` digest.

    Raises:
        ValueError: If a pin site is missing, malformed, or the sites disagree.
    """
    digests: list[str] = []
    for path in paths:
        matches = list(PIN_RE.finditer(path.read_text(encoding="utf-8")))
        if len(matches) != 1:
            raise ValueError(
                f"{path}: expected exactly one digest-pinned {IMAGE_REF} reference, "
                f"found {len(matches)}",
            )
        digests.append(matches[0].group("digest"))
    if len(set(digests)) != 1:
        raise ValueError(
            "lintro-tools pin sites disagree: " + ", ".join(sorted(set(digests))),
        )
    return digests[0]


def pinned_tag(*, sha: str) -> str:
    """Return the persistent tag for a merge commit.

    Args:
        sha: Full or abbreviated commit SHA of the merge that pinned the digest.

    Returns:
        ``pinned-<sha7>`` using the first seven hexadecimal characters.

    Raises:
        ValueError: If *sha* is shorter than seven hexadecimal characters.
    """
    normalized = sha.strip().lower()
    if len(normalized) < 7 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"invalid merge SHA: {sha!r}")
    return f"{PINNED_TAG_PREFIX}{normalized[:7]}"


def _write_output(*, changed: bool) -> None:
    """Write the workflow output when running under GitHub Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")


def _write_read_output(*, digest: str, tag: str) -> None:
    """Export the pin digest and persistent tag for GitHub Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"digest={digest}\n")
            output.write(f"sha7={tag.removeprefix(PINNED_TAG_PREFIX)}\n")
            output.write(f"pinned-tag={tag}\n")


def _read_cli() -> int:
    """Print and export the digest currently pinned on the pin sites."""
    sha = os.environ.get("GITHUB_SHA", "")
    try:
        digest = read_pin_digest()
        tag = pinned_tag(sha=sha)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _write_read_output(digest=digest, tag=tag)
    print(digest)
    return 0


def main(*, argv: list[str] | None = None) -> int:
    """Run the digest updater or reader CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--digest", help="sha256:<64 hex characters>")
    mode.add_argument(
        "--read",
        action="store_true",
        help="print the digest both pin sites currently agree on",
    )
    args = parser.parse_args(argv)
    if args.read:
        return _read_cli()
    digest = args.digest
    if not isinstance(digest, str):
        print("--digest is required unless --read is set", file=sys.stderr)
        return 2

    try:
        changed = update_digest(digest=digest)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _write_output(changed=changed)
    state = "updated" if changed else "already matches"
    print(f"lintro-tools digest {state}: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
