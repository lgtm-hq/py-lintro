#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Write the full release manifest from the release gate (#2562).

Extends the image manifest ``scripts/ci/write-release-manifest.py`` builds
(the three Docker index digests) with a ``files`` section describing every
asset the gate assembled for the GitHub Release: its SHA-256, size and the
Sigstore bundle attached next to it. The ``SHA256SUMS`` the gate wrote is
cross-checked against the computed digests so the manifest and the release
checksums can never disagree.

Usage:
    RELEASE_TAG=<tag> RUN_ID=<id> BASE_DIGEST=<sha256:...> \
        FULL_DIGEST=<sha256:...> AI_DIGEST=<sha256:...> \
        ASSETS_DIR=release-assets OUTPUT=release-manifest.json \
        python3 scripts/ci/release-gate/write_manifest.py

Exit codes:
    0 — Manifest written.
    1 — A required variable is missing, a digest is malformed, the assets
        directory is empty, or SHA256SUMS disagrees with the files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

BUNDLE_SUFFIX = ".intoto.jsonl"
CHECKSUMS_NAME = "SHA256SUMS"
IMAGE_MANIFEST_SCRIPT = (
    Path(__file__).resolve().parent.parent / "write-release-manifest.py"
)


def _load_image_manifest_module() -> Any:
    """Import the PR (b) image-manifest writer by path (hyphenated name)."""
    spec = importlib.util.spec_from_file_location(
        "write_release_manifest",
        IMAGE_MANIFEST_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {IMAGE_MANIFEST_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    """Return the hex SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksums(path: Path) -> dict[str, str]:
    """Parse a ``sha256sum`` manifest into ``{name: hex}``."""
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        name = name.lstrip("*")
        if len(digest) != 64 or not name:
            raise ValueError(f"malformed {CHECKSUMS_NAME} line: {line!r}")
        parsed[name] = digest
    return parsed


def build_files(assets_dir: Path) -> dict[str, dict[str, object]]:
    """Describe every release asset in ``assets_dir``.

    Args:
        assets_dir: The directory the gate assembled.

    Returns:
        ``{asset: {"sha256", "size", "bundle"}}`` for every asset that is not
        a bundle or the checksums file; ``bundle`` names the sidecar when one
        exists next to the asset, else ``None``.

    Raises:
        ValueError: The directory holds no assets, lacks ``SHA256SUMS``, or
            the checksums disagree with the files on disk.
    """
    if not assets_dir.is_dir():
        raise ValueError(f"ASSETS_DIR {assets_dir} is not a directory")
    checksums_path = assets_dir / CHECKSUMS_NAME
    if not checksums_path.is_file():
        raise ValueError(f"{checksums_path} is missing")
    expected = _parse_checksums(checksums_path)
    files: dict[str, dict[str, object]] = {}
    for path in sorted(assets_dir.iterdir()):
        if not path.is_file() or path.name == CHECKSUMS_NAME:
            continue
        digest = _sha256(path)
        if expected.get(path.name) != digest:
            raise ValueError(
                f"{CHECKSUMS_NAME} disagrees with {path.name}: "
                f"{expected.get(path.name)!r} != {digest!r}",
            )
        if path.name.endswith(BUNDLE_SUFFIX):
            continue
        bundle = path.with_name(path.name + BUNDLE_SUFFIX)
        files[path.name] = {
            "sha256": digest,
            "size": path.stat().st_size,
            "bundle": bundle.name if bundle.is_file() else None,
        }
    if not files:
        raise ValueError(f"no release assets found in {assets_dir}")
    missing = set(expected) - {p.name for p in assets_dir.iterdir() if p.is_file()}
    if missing:
        raise ValueError(
            f"{CHECKSUMS_NAME} lists files that are absent: {sorted(missing)}",
        )
    return files


def build_manifest(env: dict[str, str]) -> dict[str, object]:
    """Assemble the full manifest: image digests plus release files.

    Args:
        env: Environment mapping (normally ``os.environ``).

    Returns:
        The manifest document.

    Raises:
        ValueError: Any input is missing or inconsistent.
    """
    manifest: dict[str, object] = _load_image_manifest_module().build_manifest(env)
    assets_dir = env.get("ASSETS_DIR", "").strip()
    if not assets_dir:
        raise ValueError("ASSETS_DIR is required")
    manifest["files"] = build_files(Path(assets_dir))
    return manifest


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
    files = manifest["files"]
    assert isinstance(files, dict)
    print(f"Wrote {path} with {len(files)} release file(s) and 3 image digest(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
