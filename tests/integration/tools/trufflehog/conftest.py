"""Pytest configuration for trufflehog integration tests."""

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
TRUFFLEHOG_SAMPLES = SAMPLE_DIR / "tools" / "security" / "trufflehog"
CLEAN_SAMPLE = TRUFFLEHOG_SAMPLES / "trufflehog_clean.py"
VIOLATION_SAMPLE = TRUFFLEHOG_SAMPLES / "trufflehog_violations.py"


@pytest.fixture
def trufflehog_violation_file(tmp_path: Path) -> str:
    """Copy the trufflehog violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "trufflehog_violations.py"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def trufflehog_clean_file(tmp_path: Path) -> str:
    """Copy the trufflehog clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "trufflehog_clean.py"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
