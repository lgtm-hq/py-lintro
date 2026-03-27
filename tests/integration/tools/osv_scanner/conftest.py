"""Pytest configuration for osv_scanner integration tests."""

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
OSV_SCANNER_SAMPLES = SAMPLE_DIR / "tools" / "security" / "osv_scanner"
VIOLATION_SAMPLE = OSV_SCANNER_SAMPLES / "osv_scanner_violations.txt"
CLEAN_SAMPLE = OSV_SCANNER_SAMPLES / "osv_scanner_clean.txt"


@pytest.fixture
def osv_violation_file(tmp_path: Path) -> str:
    """Copy the osv_scanner violation sample to a temp directory.

    The destination is named ``requirements.txt`` so osv-scanner
    recognises it as a Python lockfile.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "requirements.txt"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def osv_clean_file(tmp_path: Path) -> str:
    """Copy the osv_scanner clean sample to a temp directory.

    The destination is named ``requirements.txt`` so osv-scanner
    recognises it as a Python lockfile.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "requirements.txt"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
