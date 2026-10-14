"""Pytest configuration for terraform integration tests.

These tests exercise the real ``terraform`` binary against committed fixtures.
They are skipped when terraform is not installed so local runs without the
binary behave like a clean skip rather than a failure.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - drives the real terraform binary; shell=False
from pathlib import Path

import pytest


def _find_project_root() -> Path:
    """Find project root by looking for pyproject.toml.

    Returns:
        Path: Path to the project root directory.

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
TERRAFORM_SAMPLES = SAMPLE_DIR / "tools" / "config" / "terraform"
VIOLATION_SAMPLE = TERRAFORM_SAMPLES / "terraform_violations.tf"
CLEAN_SAMPLE = TERRAFORM_SAMPLES / "clean" / "main.tf"
BROKEN_SAMPLE = TERRAFORM_SAMPLES / "validate_broken" / "main.tf"


def terraform_available() -> bool:
    """Return True if the ``terraform`` binary is available and runnable.

    Returns:
        bool: True when ``terraform version`` succeeds, False otherwise.
    """
    if shutil.which("terraform") is None:
        return False
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed argv from PATH, shell=False
            ["terraform", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


@pytest.fixture(autouse=True)
def _require_terraform() -> None:
    """Skip every terraform integration test when the binary is unavailable."""
    if not terraform_available():
        pytest.skip("terraform not available")


def _module_from(sample: Path, tmp_path: Path, name: str) -> str:
    """Copy a sample ``.tf`` file into a fresh module directory.

    Args:
        sample: Source ``.tf`` fixture to copy.
        tmp_path: Pytest-provided temporary directory.
        name: Name of the module subdirectory to create.

    Returns:
        str: Path to the created module directory.
    """
    module = tmp_path / name
    module.mkdir()
    shutil.copy(sample, module / "main.tf")
    return str(module)


@pytest.fixture
def terraform_violation_module(tmp_path: Path) -> str:
    """Provide a module directory holding an unformatted ``main.tf``.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        str: Path to the module directory.
    """
    return _module_from(sample=VIOLATION_SAMPLE, tmp_path=tmp_path, name="fmt")


@pytest.fixture
def terraform_clean_module(tmp_path: Path) -> str:
    """Provide a module directory holding a clean, valid ``main.tf``.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        str: Path to the module directory.
    """
    return _module_from(sample=CLEAN_SAMPLE, tmp_path=tmp_path, name="clean")


@pytest.fixture
def terraform_broken_module(tmp_path: Path) -> str:
    """Provide a module directory holding a ``main.tf`` that fails validation.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        str: Path to the module directory.
    """
    return _module_from(sample=BROKEN_SAMPLE, tmp_path=tmp_path, name="broken")
