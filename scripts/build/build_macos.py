#!/usr/bin/env python3
"""Build macOS binary using Nuitka.

This script builds a standalone binary of lintro for macOS using Nuitka.
It supports both ARM64 (Apple Silicon) and x86_64 (Intel) architectures.

Usage:
    python scripts/build/build_macos.py [--arch arm64|x86_64|universal]

Requirements:
    - Python 3.11+
    - Nuitka (install with: uv sync --group build)
    - Xcode Command Line Tools (for macOS compilation)
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
import sys
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Output directory for built binaries
OUTPUT_DIR = PROJECT_ROOT / "dist" / "nuitka"

# Packages to include in the build
INCLUDE_PACKAGES = [
    "lintro",
    "click",
    "loguru",
    "tabulate",
    "defusedxml",
    "httpx",
]

# The MCP SDK (#2577). The `mcp` extra is synced for binary builds and both
# packages are named here explicitly: nothing in lintro imports the SDK at
# module level (the server imports it lazily), so --follow-imports alone
# would leave it out and `lintro mcp` would fail at the first SDK import.
MCP_SDK_PACKAGES = [
    "mcp",
    "mcp_types",
]

# Distributions whose metadata the SDK stack reads at runtime (#2577).
# `httpx2` and `httpcore2` resolve their own version from it at import time,
# so without it `lintro mcp` fails on `PackageNotFoundError` before the
# server starts; the rest expose it lazily through ``__version__``.
INCLUDE_DISTRIBUTION_METADATA = [
    "httpx2",
    "httpcore2",
    "mcp",
    "mcp-types",
    "pydantic",
    "jsonschema",
    "attrs",
]

# Data files to include (relative to package)
INCLUDE_DATA_DIRS = [
    "lintro/assets=lintro/assets",
]

# Packages Nuitka ships as bytecode instead of compiling to C (#2514).
# Hard-coded on purpose: were these read from the environment, an unset
# variable would silently produce a compiled build again.
BYTECODE_PACKAGES = [
    "lintro",
    "pygments",
    # The MCP SDK and its pure-Python runtime stack (#2577). Shipped as
    # bytecode so bundling the SDK stays inside the #2514 compile budget;
    # pydantic_core, rpds and cryptography are extension modules and are
    # copied as-is regardless of this list.
    "mcp",
    "mcp_types",
    "pydantic",
    "pydantic_settings",
    "annotated_types",
    "typing_inspection",
    "typing_extensions",
    "starlette",
    "sse_starlette",
    "anyio",
    "sniffio",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "h11",
    "idna",
    "certifi",
    "jsonschema",
    "jsonschema_specifications",
    "referencing",
    "attrs",
    "attr",
    "jwt",
    "python_multipart",
    "multipart",
    "opentelemetry",
    "uvicorn",
]

# Non-Python data files required at runtime (not included by --include-package)
INCLUDE_DATA_FILES = [
    "lintro/tools/manifest.json=lintro/tools/manifest.json",
]


def get_default_arch() -> str:
    """Get the default architecture based on the current system.

    Returns:
        Architecture string (arm64 or x86_64).
    """
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def build_nuitka_command(*, arch: str, verbose: bool = False) -> list[str]:
    """Build the Nuitka command for a macOS onefile binary.

    Args:
        arch: Target architecture (arm64, x86_64, or universal).
        verbose: Enable verbose output during compilation.

    Returns:
        Nuitka command argv list.
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
        f"--macos-target-arch={arch}",
    ]

    for pkg in [*INCLUDE_PACKAGES, *MCP_SDK_PACKAGES]:
        cmd.append(f"--include-package={pkg}")

    cmd.append("--include-package-data=lintro")

    for distribution in INCLUDE_DISTRIBUTION_METADATA:
        cmd.append(f"--include-distribution-metadata={distribution}")

    # #2514: compiling these to C produced 1,527 C units and macOS arm64 build
    # steps of 26-28 minutes against the 25-minute cap (Intel 20-25, Linux
    # 16-21); as bytecode it is 340 units and 6-12 minutes on every runner,
    # with identical behaviour. `bytecode` is the anti-bloat plugin's mode,
    # the same one that already ships `rich` uncompiled. lintro/__main__.py is
    # the entry point (compiled as `lintro.__main__`, see below) and stays
    # compiled.
    for package in BYTECODE_PACKAGES:
        cmd.append(f"--noinclude-custom-mode={package}:bytecode")

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

    # Package mode (#2577): Nuitka adds the main program's directory to its
    # module search path, so handing it `lintro/__main__.py` made `lintro/`
    # the import root and lintro's own `lintro/mcp` shadowed the SDK's
    # top-level `mcp` package inside the binary. Compiling the package as
    # `lintro.__main__` keeps the search path at the project root.
    cmd.append("--python-flag=-m")
    cmd.append(str(PROJECT_ROOT / "lintro"))
    return cmd


def regenerate_version_artifacts() -> None:
    """Regenerate the version artifacts from their sources (#2179).

    The binary bundles ``_generated_versions.py``, ``manifest.json``, and
    ``_builtin_index.py``; regenerating before assembling the Nuitka command
    makes the build self-contained instead of trusting checkout state. A
    no-op while the artifacts are committed; load-bearing once they stop
    being committed (epic #2176 phase 4). The ``INCLUDE_DATA_FILES``
    existence guards below remain the backstop.

    Raises:
        subprocess.CalledProcessError: If either generator fails.
    """
    for script in (
        "scripts/ci/generate-tool-versions.py",
        "scripts/ci/generate-builtin-tool-index.py",
    ):
        subprocess.run(  # nosec B603 - fixed argv, repo-owned script, shell=False
            [sys.executable, str(PROJECT_ROOT / script)],
            cwd=PROJECT_ROOT,
            check=True,
        )


def build_macos_binary(arch: str = "arm64", verbose: bool = False) -> int:
    """Build a standalone macOS binary using Nuitka.

    Args:
        arch: Target architecture (arm64, x86_64, or universal).
        verbose: Enable verbose output during compilation.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    print(f"Building lintro for macOS ({arch})...")
    print(f"Output directory: {OUTPUT_DIR}")

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        regenerate_version_artifacts()
        cmd = build_nuitka_command(arch=arch, verbose=verbose)
    except subprocess.CalledProcessError as exc:
        print(f"Version artifact generation failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            cmd,
            cwd=PROJECT_ROOT,
            check=True,
        )
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


def _doctor_check_error(result: subprocess.CompletedProcess[str]) -> str | None:
    """Validate ``lintro doctor --json`` output from binary verification.

    Args:
        result: Completed doctor subprocess result.

    Returns:
        Error message when verification failed, otherwise ``None``.
    """
    stderr = result.stderr or ""
    crash_markers = ("Traceback", "FileNotFoundError", "manifest.json")
    if any(marker in stderr for marker in crash_markers):
        return f"Doctor check failed: {stderr}"

    stdout = (result.stdout or "").strip()
    if not stdout:
        return "Doctor check failed: JSON output is empty"

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return f"Doctor check failed: invalid JSON ({exc})"

    if not isinstance(data, dict) or not data or "tools" not in data:
        return "Doctor check failed: JSON output is empty or missing 'tools'"

    if result.returncode not in (0, 1):
        detail = stderr or stdout[:200]
        return (
            f"Doctor check failed with exit code {result.returncode}: {detail}"
        )

    return None


def verify_binary() -> bool:
    """Verify the built binary works correctly.

    Returns:
        True if verification passed, False otherwise.
    """
    binary_path = OUTPUT_DIR / "lintro"

    if not binary_path.exists():
        print(f"Binary not found at {binary_path}", file=sys.stderr)
        return False

    print(f"\nVerifying binary at {binary_path}...")

    # Check version
    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            [str(binary_path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"Version: {result.stdout.strip()}")
        else:
            print(f"Version check failed: {result.stderr}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("Version check timed out", file=sys.stderr)
        return False

    # Check help
    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            [str(binary_path), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print("Help command: OK")
        else:
            print(f"Help check failed: {result.stderr}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("Help check timed out", file=sys.stderr)
        return False

    # Check doctor loads manifest.json (required for tool registry)
    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            [str(binary_path), "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("Doctor check timed out", file=sys.stderr)
        return False
    else:
        doctor_error = _doctor_check_error(result)
        if doctor_error is not None:
            print(doctor_error, file=sys.stderr)
            return False
        print("Doctor command: OK")

    # Check file size
    size_mb = binary_path.stat().st_size / (1024 * 1024)
    print(f"Binary size: {size_mb:.1f} MB")

    if size_mb > 100:
        print("Warning: Binary is larger than expected (>100MB)", file=sys.stderr)

    return True


def main() -> int:
    """Main entry point for the build script.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        description="Build lintro macOS binary using Nuitka",
    )
    parser.add_argument(
        "--arch",
        choices=["arm64", "x86_64", "universal"],
        default=get_default_arch(),
        help="Target architecture (default: current system)",
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

    # Clean if requested
    if args.clean and OUTPUT_DIR.exists():
        print(f"Cleaning {OUTPUT_DIR}...")
        shutil.rmtree(OUTPUT_DIR)

    # Build the binary
    if args.arch == "universal":
        # Build for both architectures
        for target_arch in ["arm64", "x86_64"]:
            exit_code = build_macos_binary(arch=target_arch, verbose=args.verbose)
            if exit_code != 0:
                return exit_code
            # Rename to include architecture
            binary_path = OUTPUT_DIR / "lintro"
            if binary_path.exists():
                binary_path.rename(OUTPUT_DIR / f"lintro-{target_arch}")
        print("\nUniversal build complete. Binaries:")
        print(f"  - {OUTPUT_DIR}/lintro-arm64")
        print(f"  - {OUTPUT_DIR}/lintro-x86_64")
        return 0
    else:
        exit_code = build_macos_binary(arch=args.arch, verbose=args.verbose)
        if exit_code != 0:
            return exit_code

    # Verify unless skipped
    if not args.skip_verify:
        if not verify_binary():
            return 1

    print("\nBuild complete!")
    print(f"Binary location: {OUTPUT_DIR / 'lintro'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
