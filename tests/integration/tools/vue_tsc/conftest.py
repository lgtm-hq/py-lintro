"""Pytest configuration for vue-tsc integration tests."""

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
