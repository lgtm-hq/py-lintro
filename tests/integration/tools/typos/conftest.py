"""Pytest configuration for typos integration tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def _find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml.

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


SAMPLE_DIR = _find_project_root() / "test_samples"
TYPOS_SAMPLES = SAMPLE_DIR / "tools" / "spelling" / "typos"
CLEAN_SAMPLE = TYPOS_SAMPLES / "typos_clean.txt"
VIOLATION_SAMPLE = TYPOS_SAMPLES / "typos_violations.txt"


@pytest.fixture
def typos_violation_file(tmp_path: Path) -> str:
    """Copy the typos violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "typos_violations.txt"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def typos_clean_file(tmp_path: Path) -> str:
    """Copy the typos clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "typos_clean.txt"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
