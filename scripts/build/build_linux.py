#!/usr/bin/env python3
"""Build a standalone Linux binary using Nuitka.

This mirrors ``scripts/build/build_macos.py`` for Linux. Nuitka compiles a
self-contained onefile executable that embeds the Python runtime and every
``[full]`` tool, so npm consumers need no Python installed.

Unlike macOS, Linux has no cross-arch flag: the binary targets the host
architecture, so arm64 and x86_64 are produced on their respective runners.

Usage:
    python scripts/build/build_linux.py [--arch arm64|x86_64]

Requirements:
    - Python 3.11+
    - Nuitka (install with: uv sync --group build)
    - A C toolchain (gcc/patchelf) for onefile compression
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Project root directory.
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Output directory for built binaries.
OUTPUT_DIR = PROJECT_ROOT / "dist" / "nuitka"

# Packages to include in the build (kept in sync with build_macos.py).
INCLUDE_PACKAGES = [
    "lintro",
    "click",
    "loguru",
    "tabulate",
    "defusedxml",
    "httpx",
]

# Directory data files to include (relative to package).
INCLUDE_DATA_DIRS = [
    "lintro/assets=lintro/assets",
]

# Non-Python data files required at runtime.
INCLUDE_DATA_FILES = [
    "lintro/tools/manifest.json=lintro/tools/manifest.json",
]

# Architectures Nuitka can target natively on Linux.
SUPPORTED_ARCHES = ("arm64", "x86_64")


def get_default_arch() -> str:
    """Get the default architecture based on the current system.

    Returns:
        Architecture string (``arm64`` or ``x86_64``).
    """
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def build_nuitka_command(*, verbose: bool = False) -> list[str]:
    """Build the Nuitka command for a Linux onefile binary.

    The host architecture is used implicitly; Linux offers no cross-arch
    equivalent to macOS's ``--macos-target-arch``.

    Args:
        verbose: Enable verbose output during compilation.

    Returns:
        Nuitka command argv list.

    Raises:
        FileNotFoundError: If a required runtime data file is missing.
    """
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        f"--output-dir={OUTPUT_DIR}",
        "--output-filename=lintro",
        "--follow-imports",
        "--assume-yes-for-downloads",
    ]

    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")

    cmd.append("--include-package-data=lintro")

    for data_dir in INCLUDE_DATA_DIRS:
        data_path = PROJECT_ROOT / data_dir.split("=")[0]
        if data_path.exists():
            cmd.append(f"--include-data-dir={data_dir}")

    for data_file in INCLUDE_DATA_FILES:
        data_path = PROJECT_ROOT / data_file.split("=")[0]
        if not data_path.exists():
            msg = f"Required runtime data file missing for Nuitka build: {data_path}"
            raise FileNotFoundError(msg)
        cmd.append(f"--include-data-files={data_file}")

    if verbose:
        cmd.append("--verbose")

    cmd.append(str(PROJECT_ROOT / "lintro" / "__main__.py"))
    return cmd


def build_linux_binary(*, verbose: bool = False) -> int:
    """Build a standalone Linux binary using Nuitka.

    Args:
        verbose: Enable verbose output during compilation.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    print(f"Building lintro for Linux ({get_default_arch()})...")
    print(f"Output directory: {OUTPUT_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        cmd = build_nuitka_command(verbose=verbose)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        print(f"Build failed with exit code {e.returncode}", file=sys.stderr)
        return e.returncode
    except FileNotFoundError:
        print(
            "Nuitka not found. Install with: uv sync --group build",
            file=sys.stderr,
        )
        return 1


def verify_binary() -> bool:
    """Verify the built binary responds to ``--version`` and ``--help``.

    Returns:
        True if verification passed, False otherwise.
    """
    binary_path = OUTPUT_DIR / "lintro"

    if not binary_path.exists():
        print(f"Binary not found at {binary_path}", file=sys.stderr)
        return False

    print(f"\nVerifying binary at {binary_path}...")

    for check_args, label in (
        (["--version"], "Version"),
        (["--help"], "Help command"),
    ):
        try:
            result = subprocess.run(
                [str(binary_path), *check_args],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            print(f"{label} check timed out", file=sys.stderr)
            return False
        if result.returncode != 0:
            print(f"{label} check failed: {result.stderr}", file=sys.stderr)
            return False
        print(f"{label}: OK")

    size_mb = binary_path.stat().st_size / (1024 * 1024)
    print(f"Binary size: {size_mb:.1f} MB")
    if size_mb > 100:
        print("Warning: Binary is larger than expected (>100MB)", file=sys.stderr)

    return True


def main() -> int:
    """Main entry point for the Linux build script.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        description="Build lintro Linux binary using Nuitka",
    )
    parser.add_argument(
        "--arch",
        choices=list(SUPPORTED_ARCHES),
        default=get_default_arch(),
        help="Target architecture (informational; must match the host).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip binary verification after build",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean output directory before build",
    )

    args = parser.parse_args()

    host_arch = get_default_arch()
    if args.arch != host_arch:
        print(
            f"Refusing to build {args.arch} on a {host_arch} host: Linux "
            "builds are native-only (no cross-compilation).",
            file=sys.stderr,
        )
        return 1

    if args.clean and OUTPUT_DIR.exists():
        print(f"Cleaning {OUTPUT_DIR}...")
        shutil.rmtree(OUTPUT_DIR)

    exit_code = build_linux_binary(verbose=args.verbose)
    if exit_code != 0:
        return exit_code

    if not args.skip_verify and not verify_binary():
        return 1

    print("\nBuild complete!")
    print(f"Binary location: {OUTPUT_DIR / 'lintro'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
