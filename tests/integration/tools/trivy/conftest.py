"""Pytest configuration for trivy integration tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


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
TRIVY_SAMPLES = SAMPLE_DIR / "tools" / "security" / "trivy"
VIOLATION_SAMPLE = TRIVY_SAMPLES / "trivy_violations.txt"
CLEAN_SAMPLE = TRIVY_SAMPLES / "trivy_clean.txt"

# Substring of the plugin's non-fatal "no local vulnerability DB" message.
# Trivy runs hermetically (``--skip-db-update``), so a machine without a cached
# database reports this instead of scanning; tests skip rather than fail.
DB_MISSING_MARKER = "vulnerability database not found"


@pytest.fixture
def trivy_violation_file(tmp_path: Path) -> str:
    """Copy the trivy violation sample to a temp directory.

    The destination is named ``requirements.txt`` so trivy recognises it as a
    Python dependency manifest.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "requirements.txt"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def trivy_clean_file(tmp_path: Path) -> str:
    """Copy the trivy clean sample to a temp directory.

    The destination is named ``requirements.txt`` so trivy recognises it as a
    Python dependency manifest.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "requirements.txt"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
