"""Pytest configuration for pip_audit integration tests."""

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
PIP_AUDIT_SAMPLES = SAMPLE_DIR / "tools" / "security" / "pip_audit"
VIOLATION_SAMPLE = PIP_AUDIT_SAMPLES / "pip_audit_violations.txt"
CLEAN_SAMPLE = PIP_AUDIT_SAMPLES / "pip_audit_clean.txt"


@pytest.fixture
def pip_audit_violation_file(tmp_path: Path) -> str:
    """Copy the pip-audit violation sample to a temp directory.

    The destination is named ``requirements.txt`` so pip-audit's
    ``requirements*.txt`` discovery recognises it as a lockfile.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "requirements.txt"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def pip_audit_clean_file(tmp_path: Path) -> str:
    """Copy the pip-audit clean sample to a temp directory.

    The destination is named ``requirements.txt`` so pip-audit's
    ``requirements*.txt`` discovery recognises it as a lockfile.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "requirements.txt"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)
