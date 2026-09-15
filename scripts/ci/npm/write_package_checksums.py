#!/usr/bin/env python3
"""Write a SHA256SUMS manifest over every file npm would pack.

lgtm-ci's ``reusable-publish-npm-set.yml`` verifies the staged package set
before ``npm pack``: every file ``npm pack --dry-run`` reports for every
package must carry a manifest entry whose digest matches, and every entry
must verify against the signer workflow's attestation. This script produces
that manifest for the four ``npm/`` packages, and the publish workflow
attests it (``subject-checksums``), so the reusable can prove the bytes it
packs are the bytes the stage job verified and signed (#2632).

Usage:
    python scripts/ci/npm/write_package_checksums.py \
        --packages-dir npm --output npm/SHA256SUMS

Entries are ``<sha256>  <package>/<file>`` with paths relative to the
packages directory, one per packed file, sorted. The file set is asked from
npm itself (``npm pack --dry-run --json --ignore-scripts``), which writes no
tarball and runs no package scripts, so the manifest cannot drift from what
``npm publish`` ships.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed npm argv, shell=False
import sys
from pathlib import Path


class PackError(RuntimeError):
    """Raised when npm cannot enumerate a package's file set."""


def packed_files(package_dir: Path, *, npm: str = "npm") -> list[str]:
    """Return the paths ``npm pack`` would ship for one package.

    Args:
        package_dir: Directory holding the package's ``package.json``.
        npm: npm executable name (overridable in tests).

    Returns:
        Package-relative file paths, in npm's order.

    Raises:
        PackError: When npm fails, reports no files, or emits unparseable
            JSON. An unknown file set cannot be verified, so this fails closed.
    """
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [npm, "pack", "--dry-run", "--json", "--ignore-scripts"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"could not run {npm} in {package_dir}: {exc}"
        raise PackError(msg) from exc
    if result.returncode != 0:
        msg = f"npm pack --dry-run failed for {package_dir}: {result.stderr.strip()}"
        raise PackError(msg)
    try:
        report = json.loads(result.stdout)
        files = [str(entry["path"]) for entry in report[0]["files"]]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        msg = f"could not parse the npm pack file list for {package_dir}: {exc}"
        raise PackError(msg) from exc
    if not files:
        msg = f"npm pack reported no files for {package_dir}; refusing an empty package"
        raise PackError(msg)
    return files


def sha256_of(path: Path) -> str:
    """Return the hex sha256 digest of a file.

    Args:
        path: File to hash.

    Returns:
        Lower-case hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_dirs(packages_dir: Path) -> list[Path]:
    """Return every package subdirectory, sorted by name.

    Args:
        packages_dir: Directory containing one subdirectory per package.

    Returns:
        Subdirectories that carry a ``package.json``.
    """
    return sorted(
        child
        for child in packages_dir.iterdir()
        if child.is_dir() and (child / "package.json").is_file()
    )


def manifest_lines(packages_dir: Path, *, npm: str = "npm") -> list[str]:
    """Build the manifest lines for every package under ``packages_dir``.

    Args:
        packages_dir: Directory containing one subdirectory per package.
        npm: npm executable name (overridable in tests).

    Returns:
        Sorted ``<sha256>  <package>/<file>`` lines.

    Raises:
        PackError: When no package is found or npm cannot enumerate one.
        FileNotFoundError: When npm lists a file that is not on disk.
    """
    packages = package_dirs(packages_dir)
    if not packages:
        msg = f"no package directories (with package.json) under {packages_dir}"
        raise PackError(msg)
    lines: list[str] = []
    for package in packages:
        for rel in packed_files(package, npm=npm):
            file = package / rel
            if not file.is_file():
                msg = f"npm would pack {package.name}/{rel} but it does not exist"
                raise FileNotFoundError(msg)
            lines.append(f"{sha256_of(file)}  {package.name}/{rel}")
    return sorted(lines, key=lambda line: line.split("  ", 1)[1])


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector.

    Returns:
        Process exit code (0 on success, 1 on any verification failure).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--packages-dir",
        required=True,
        type=Path,
        help="Directory containing one subdirectory per npm package.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Manifest path to write (sha256sum format).",
    )
    args = parser.parse_args(argv)

    npm = os.environ.get("NPM_CMD", "npm")
    try:
        lines = manifest_lines(args.packages_dir.resolve(), npm=npm)
    except (PackError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line)
    print(f"Wrote {len(lines)} checksums to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
