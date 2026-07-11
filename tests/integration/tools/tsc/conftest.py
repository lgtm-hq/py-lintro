"""Pytest configuration for tsc integration tests."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest


def tsc_is_available() -> bool:
    """Check if tsc is installed and actually works.

    This checks both that the command exists AND that it executes successfully,
    which handles cases where a wrapper script exists but the underlying
    tool isn't installed (e.g., Docker image not yet rebuilt).

    Returns:
        True if tsc is installed and working, False otherwise.
    """
    if shutil.which("tsc") is None:
        return False
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["tsc", "--version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


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
TSC_SAMPLES = SAMPLE_DIR / "tools" / "typescript" / "tsc"
CLEAN_SAMPLE = TSC_SAMPLES / "tsc_clean.ts"
VIOLATION_SAMPLE = TSC_SAMPLES / "tsc_violations.ts"


@pytest.fixture
def tsc_violation_file(tmp_path: Path) -> str:
    """Copy the tsc violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "tsc_violations.ts"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def tsc_clean_file(tmp_path: Path) -> str:
    """Copy the tsc clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "tsc_clean.ts"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
