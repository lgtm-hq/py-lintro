"""Shared fixtures for import-linter integration tests.

These tests require the ``lint-imports`` binary (the ``import-linter``
distribution) to be installed and available in PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

SAMPLE_DIR = Path(__file__).resolve().parents[4] / "test_samples"
IMPORT_LINTER_SAMPLES = SAMPLE_DIR / "tools" / "python" / "import_linter"

VIOLATIONS_SAMPLE = IMPORT_LINTER_SAMPLES / "violations"
CLEAN_SAMPLE = IMPORT_LINTER_SAMPLES / "clean"

for sample in (VIOLATIONS_SAMPLE, CLEAN_SAMPLE):
    if not sample.is_dir():
        raise FileNotFoundError(f"import-linter sample project not found: {sample}")


@pytest.fixture
def broken_contract_project(tmp_path: Path) -> str:
    """Stage the sample project whose layers contract is deliberately broken.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "violations"
    shutil.copytree(VIOLATIONS_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def kept_contract_project(tmp_path: Path) -> str:
    """Stage the sample project whose layers contract is honoured.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "clean"
    shutil.copytree(CLEAN_SAMPLE, dst)
    return str(dst)
