"""Shared fixtures for pylint integration tests.

These tests require the ``pylint`` binary to be installed and on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

SAMPLE_DIR = Path(__file__).resolve().parents[4] / "test_samples"
PYLINT_SAMPLES = SAMPLE_DIR / "tools" / "python" / "pylint"

DUPLICATE_SAMPLE = PYLINT_SAMPLES / "duplicate"
CLEAN_SAMPLE = PYLINT_SAMPLES / "clean"

for sample in (DUPLICATE_SAMPLE, CLEAN_SAMPLE):
    if not sample.is_dir():
        raise FileNotFoundError(f"pylint sample project not found: {sample}")


@pytest.fixture
def duplicate_code_project(tmp_path: Path) -> str:
    """Stage the sample whose two modules share a 15-line block.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "duplicate"
    shutil.copytree(DUPLICATE_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def clean_project(tmp_path: Path) -> str:
    """Stage the sample whose modules share nothing.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "clean"
    shutil.copytree(CLEAN_SAMPLE, dst)
    return str(dst)
