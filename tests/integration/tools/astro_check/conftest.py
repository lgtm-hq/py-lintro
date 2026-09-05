"""Pytest configuration for astro-check integration tests."""

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
ASTRO_CHECK_SAMPLES = SAMPLE_DIR / "tools" / "web" / "astro_check"
CLEAN_SAMPLE = ASTRO_CHECK_SAMPLES / "astro_check_clean.astro"
VIOLATION_SAMPLE = ASTRO_CHECK_SAMPLES / "astro_check_violations.astro"


@pytest.fixture
def astro_check_violation_file(tmp_path: Path) -> str:
    """Copy the astro check violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "astro_check_violations.astro"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def astro_check_clean_file(tmp_path: Path) -> str:
    """Copy the astro check clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "astro_check_clean.astro"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
