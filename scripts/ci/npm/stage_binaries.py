#!/usr/bin/env python3
"""Stage downloaded platform binaries into the npm package tree.

The build workflow uploads one artifact per platform
(``lintro-macos-arm64``, ``lintro-linux-x64``, ...). This script copies each
into the matching ``npm/<platform>/bin/lintro`` slot, marks it executable,
and fails loudly if any expected binary is missing.

Usage:
    python scripts/ci/npm/stage_binaries.py --artifacts-dir <download-root>

The artifacts directory is expected to contain one subdirectory per
downloaded artifact (the default layout of actions/download-artifact when
no ``merge-multiple`` is used), each holding the renamed binary file.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
NPM_DIR = PROJECT_ROOT / "npm"

# Map: (artifact name, binary filename) -> npm platform package directory.
# Artifact names mirror the upload steps in build-binary.yml; npm platform
# keys follow Node's platform-arch convention (darwin/x64, linux/arm64, ...).
BINARY_MAP: dict[str, str] = {
    "lintro-macos-arm64": "darwin-arm64",
    "lintro-macos-x86_64": "darwin-x64",
    "lintro-linux-arm64": "linux-arm64",
    "lintro-linux-x64": "linux-x64",
}


def _find_binary(artifacts_dir: Path, artifact_name: str) -> Path | None:
    """Locate the binary file for a given artifact.

    Looks first for ``<artifacts_dir>/<artifact_name>/<artifact_name>`` and
    falls back to a file matching the artifact name beneath the artifacts
    directory, but only when exactly one such file exists — multiple
    candidates (e.g. leftovers from an earlier run) are ambiguous and must
    fail rather than risk staging a stale binary.

    Args:
        artifacts_dir: Root directory holding downloaded artifacts.
        artifact_name: The artifact/binary filename to find.

    Returns:
        The resolved binary path, or ``None`` when not found.

    Raises:
        RuntimeError: When multiple candidate files match the artifact name.
    """
    direct = artifacts_dir / artifact_name / artifact_name
    if direct.is_file():
        return direct
    matches = sorted(p for p in artifacts_dir.rglob(artifact_name) if p.is_file())
    if len(matches) > 1:
        listing = ", ".join(str(p) for p in matches)
        msg = (
            f"Ambiguous binary artifact '{artifact_name}': multiple candidate "
            f"files found under {artifacts_dir} ({listing}). Refusing to pick "
            "one; clean the artifacts directory and retry."
        )
        raise RuntimeError(msg)
    return matches[0] if matches else None


def stage_binaries(artifacts_dir: Path, *, npm_dir: Path = NPM_DIR) -> list[str]:
    """Copy every platform binary into its npm package's ``bin/lintro`` slot.

    Args:
        artifacts_dir: Root directory holding downloaded artifacts.
        npm_dir: Root ``npm/`` directory.

    Returns:
        The list of platform keys that were staged.

    Raises:
        FileNotFoundError: When an expected binary artifact is missing.
    """
    staged: list[str] = []
    for artifact_name, platform_key in BINARY_MAP.items():
        source = _find_binary(artifacts_dir, artifact_name)
        if source is None:
            msg = (
                f"Missing binary artifact '{artifact_name}' under "
                f"{artifacts_dir}. Cannot stage npm package '{platform_key}'."
            )
            raise FileNotFoundError(msg)

        dest = npm_dir / platform_key / "bin" / "lintro"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        try:
            shown = dest.relative_to(PROJECT_ROOT)
        except ValueError:
            shown = dest
        print(f"Staged {source} -> {shown}")
        staged.append(platform_key)
    return staged


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector.

    Returns:
        Process exit code (0 on success, 1 on missing or ambiguous
        artifacts).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        type=Path,
        help="Directory containing downloaded platform binary artifacts.",
    )
    args = parser.parse_args(argv)

    try:
        staged = stage_binaries(args.artifacts_dir.resolve())
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Staged {len(staged)} platform binaries: {', '.join(staged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
