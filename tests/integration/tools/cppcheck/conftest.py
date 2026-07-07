"""Shared fixtures for cppcheck integration tests.

These tests require cppcheck to be installed and available in PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

SAMPLE_DIR = Path(__file__).parent.parent.parent.parent.parent / "test_samples"
CPPCHECK_SAMPLES = SAMPLE_DIR / "tools" / "c" / "cppcheck"

if not CPPCHECK_SAMPLES.exists():
    raise FileNotFoundError(
        f"Cppcheck test samples not found at: {CPPCHECK_SAMPLES}",
    )

VIOLATIONS_SAMPLE = CPPCHECK_SAMPLES / "cppcheck_violations.c"
CLEAN_SAMPLE = CPPCHECK_SAMPLES / "cppcheck_clean.c"

for sample in (VIOLATIONS_SAMPLE, CLEAN_SAMPLE):
    if not sample.exists():
        raise FileNotFoundError(f"Cppcheck sample file not found: {sample}")


@pytest.fixture
def cppcheck_violation_file(tmp_path: Path) -> str:
    """Create a temporary copy of the cppcheck violations sample.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "cppcheck_violations.c"
    shutil.copy(VIOLATIONS_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def cppcheck_clean_file(tmp_path: Path) -> str:
    """Create a temporary copy of the clean cppcheck sample.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "cppcheck_clean.c"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
