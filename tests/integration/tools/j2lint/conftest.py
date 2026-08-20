"""Pytest configuration for j2lint integration tests."""

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
J2LINT_SAMPLES = SAMPLE_DIR / "tools" / "config" / "j2lint"
CLEAN_SAMPLE = J2LINT_SAMPLES / "j2lint_clean.j2"
VIOLATION_SAMPLE = J2LINT_SAMPLES / "j2lint_violations.j2"


@pytest.fixture
def j2lint_violation_file(tmp_path: Path) -> str:
    """Copy the j2lint violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "j2lint_violations.j2"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def j2lint_clean_file(tmp_path: Path) -> str:
    """Copy the j2lint clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "j2lint_clean.j2"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
