"""Shared fixtures for dotenv-linter integration tests.

These tests require the ``dotenv-linter`` binary to be installed and available
in PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# Path to test samples
SAMPLE_DIR = Path(__file__).parent.parent.parent.parent.parent / "test_samples"
DOTENV_LINTER_SAMPLES = SAMPLE_DIR / "tools" / "dotenv" / "dotenv_linter"

# Validate sample paths exist at import time for clearer error messages
if not DOTENV_LINTER_SAMPLES.exists():
    raise FileNotFoundError(
        f"dotenv-linter test samples not found at: {DOTENV_LINTER_SAMPLES}",
    )

VIOLATIONS_SAMPLE = DOTENV_LINTER_SAMPLES / "dotenv_linter_violations.env"
CLEAN_SAMPLE = DOTENV_LINTER_SAMPLES / "dotenv_linter_clean.env"

for sample in (VIOLATIONS_SAMPLE, CLEAN_SAMPLE):
    if not sample.exists():
        raise FileNotFoundError(f"dotenv-linter sample file not found: {sample}")


@pytest.fixture
def dotenv_violation_file(tmp_path: Path) -> str:
    """Create a temporary ``.env`` copy of the violations sample.

    dotenv-linter only lints files whose name matches the ``.env`` patterns,
    so the copy is named ``.env`` rather than keeping the sample suffix.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / ".env"
    shutil.copy(VIOLATIONS_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def dotenv_clean_file(tmp_path: Path) -> str:
    """Create a temporary ``.env`` copy of the clean sample.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / ".env"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
