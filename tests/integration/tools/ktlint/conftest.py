"""Pytest configuration for ktlint integration tests.

These tests require ktlint (and a JVM) on ``PATH``; they are skipped
otherwise.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lintro.tools.definitions.ktlint import KtlintPlugin


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
KTLINT_SAMPLES = SAMPLE_DIR / "tools" / "kotlin" / "ktlint"
CLEAN_SAMPLE = KTLINT_SAMPLES / "Clean.kt"
VIOLATION_SAMPLE = KTLINT_SAMPLES / "ktlint_violations.kt"
VIOLATION_SCRIPT_SAMPLE = KTLINT_SAMPLES / "ktlint_violations.kts"


@pytest.fixture
def ktlint_plugin() -> KtlintPlugin:
    """Provide a real KtlintPlugin instance.

    Returns:
        A plugin instance for integration testing.
    """
    return KtlintPlugin()


@pytest.fixture
def ktlint_clean_file(tmp_path: Path) -> str:
    """Copy the clean Kotlin sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "Clean.kt"
    shutil.copy(CLEAN_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def ktlint_violation_file(tmp_path: Path) -> str:
    """Copy the violation sample under a class-matching file name.

    Naming the copy after its declared class keeps the non-auto-correctable
    ``standard:filename`` rule from firing, so only formatting violations
    remain.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "Example.kt"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def ktlint_misnamed_file(tmp_path: Path) -> str:
    """Copy the violation sample under a name that violates ``filename``.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "lowercase_name.kt"
    shutil.copy(VIOLATION_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def ktlint_violation_script(tmp_path: Path) -> str:
    """Copy the Kotlin Script violation sample to a temp directory.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied file as a string.
    """
    dst = tmp_path / "build.gradle.kts"
    shutil.copy(VIOLATION_SCRIPT_SAMPLE, dst)
    return str(dst)
