"""Pytest configuration for vue-tsc integration tests."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest


def vue_tsc_is_available() -> bool:
    """Check if vue-tsc is installed and actually works.

    This checks both that the command exists AND that it executes successfully,
    which handles cases where a wrapper script exists but the underlying
    tool isn't installed. Also checks bunx/npx fallbacks.

    Returns:
        True if vue-tsc is installed and working, False otherwise.
    """
    # Try direct vue-tsc command first
    if shutil.which("vue-tsc") is not None:
        try:
            result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
                ["vue-tsc", "--version"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Try bunx fallback
    if shutil.which("bunx") is not None:
        try:
            result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
                ["bunx", "vue-tsc", "--version"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Try npx fallback
    if shutil.which("npx") is not None:
        try:
            result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
                ["npx", "vue-tsc", "--version"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

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
VUE_TSC_SAMPLES = SAMPLE_DIR / "tools" / "web" / "vue_tsc"
CLEAN_SAMPLE = VUE_TSC_SAMPLES / "vue_tsc_clean.vue"
VIOLATION_SAMPLE = VUE_TSC_SAMPLES / "vue_tsc_violations.vue"


@pytest.fixture
def vue_tsc_violation_file(tmp_path: Path) -> str:
    """Copy the vue-tsc violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "vue_tsc_violations.vue"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def vue_tsc_clean_file(tmp_path: Path) -> str:
    """Copy the vue-tsc clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "vue_tsc_clean.vue"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
