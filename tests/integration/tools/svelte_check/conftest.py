"""Pytest configuration for svelte-check integration tests."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from collections.abc import Sequence
from pathlib import Path

import pytest


def _check_command_version(cmd: Sequence[str], timeout: int) -> bool:
    """Run a version command and return whether it succeeded.

    Args:
        cmd: Command and arguments to execute.
        timeout: Timeout in seconds for the subprocess.

    Returns:
        True if the command exits with returncode 0, False otherwise.
    """
    try:
        result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def svelte_check_is_available() -> bool:
    """Check if svelte-check is installed and actually works.

    This checks both that the command exists AND that it executes successfully,
    which handles cases where a wrapper script exists but the underlying
    tool isn't installed. Also checks bunx/npx fallbacks.

    Returns:
        True if svelte-check is installed and working, False otherwise.
    """
    if shutil.which("svelte-check") is not None and _check_command_version(
        ["svelte-check", "--version"],
        timeout=10,
    ):
        return True

    if shutil.which("bunx") is not None and _check_command_version(
        ["bunx", "svelte-check", "--version"],
        timeout=30,
    ):
        return True

    return shutil.which("npx") is not None and _check_command_version(
        ["npx", "svelte-check", "--version"],
        timeout=30,
    )


def _find_project_root() -> Path:
    """Find project root by looking for pyproject.toml.

    Returns:
        Path to the project root directory.

    Raises:
        RuntimeError: If pyproject.toml is not found in any parent directory.
    """
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("pyproject.toml not found in parent directories")


# Paths to test samples
SAMPLE_DIR = _find_project_root() / "test_samples"
SVELTE_CHECK_SAMPLES = SAMPLE_DIR / "tools" / "web" / "svelte_check"
CLEAN_SAMPLE = SVELTE_CHECK_SAMPLES / "svelte_check_clean.svelte"
VIOLATION_SAMPLE = SVELTE_CHECK_SAMPLES / "svelte_check_violations.svelte"


@pytest.fixture
def svelte_check_violation_file(tmp_path: Path) -> str:
    """Copy the svelte-check violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "svelte_check_violations.svelte"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def svelte_check_clean_file(tmp_path: Path) -> str:
    """Copy the svelte-check clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "svelte_check_clean.svelte"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
