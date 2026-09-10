"""Shared fixtures for checkov integration tests.

These tests require checkov to be installed and available in PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

SAMPLE_DIR = Path(__file__).parent.parent.parent.parent.parent / "test_samples"
CHECKOV_SAMPLES = SAMPLE_DIR / "tools" / "terraform" / "checkov"

if not CHECKOV_SAMPLES.exists():
    raise FileNotFoundError(
        f"Checkov test samples not found at: {CHECKOV_SAMPLES}",
    )

VIOLATIONS_SAMPLE = CHECKOV_SAMPLES / "checkov_violations.tf"
CLEAN_SAMPLE = CHECKOV_SAMPLES / "checkov_clean.tf"

for sample in (VIOLATIONS_SAMPLE, CLEAN_SAMPLE):
    if not sample.exists():
        raise FileNotFoundError(f"Checkov sample file not found: {sample}")


@pytest.fixture
def checkov_violation_file(tmp_path: Path) -> str:
    """Create a temporary copy of the checkov violations sample.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "checkov_violations.tf"
    shutil.copy(VIOLATIONS_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def checkov_clean_file(tmp_path: Path) -> str:
    """Create a temporary copy of the clean checkov sample.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "checkov_clean.tf"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
