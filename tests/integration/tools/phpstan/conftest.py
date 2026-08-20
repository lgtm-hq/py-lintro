"""Pytest configuration for phpstan integration tests."""

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
PHPSTAN_SAMPLES = SAMPLE_DIR / "tools" / "php" / "phpstan"
CLEAN_SAMPLE = PHPSTAN_SAMPLES / "phpstan_clean.php"
VIOLATION_SAMPLE = PHPSTAN_SAMPLES / "phpstan_violations.php"


@pytest.fixture
def phpstan_violation_file(tmp_path: Path) -> str:
    """Copy the phpstan violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "phpstan_violations.php"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def phpstan_clean_file(tmp_path: Path) -> str:
    """Copy the phpstan clean sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "phpstan_clean.php"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def phpstan_bare_file(tmp_path: Path) -> str:
    """Write a standalone PHP file that has no autoloader or namespace.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the written file as a string.
    """
    dst = tmp_path / "phpstan_bare.php"
    dst.write_text("<?php\n$greeting = 'hello';\necho $greeting;\n")
    return str(dst)
