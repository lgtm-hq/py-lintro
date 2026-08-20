"""Pytest configuration for checkov integration tests."""

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
CHECKOV_SAMPLES = SAMPLE_DIR / "tools" / "terraform" / "checkov"
CLEAN_SAMPLE = CHECKOV_SAMPLES / "checkov_clean.tf"
VIOLATION_SAMPLE = CHECKOV_SAMPLES / "checkov_violations.tf"


@pytest.fixture
def checkov_violation_file(tmp_path: Path) -> str:
    """Copy the checkov violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "checkov_violations.tf"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def checkov_clean_file(tmp_path: Path) -> str:
    """Copy the checkov clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "checkov_clean.tf"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
