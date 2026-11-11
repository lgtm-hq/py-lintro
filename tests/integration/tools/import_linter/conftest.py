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
UNMATCHED_IGNORE_SAMPLE = IMPORT_LINTER_SAMPLES / "unmatched_ignore"
DUPLICATE_NAMES_SAMPLE = IMPORT_LINTER_SAMPLES / "duplicate_names"
ACYCLIC_SIBLINGS_SAMPLE = IMPORT_LINTER_SAMPLES / "acyclic_siblings"

for sample in (
    VIOLATIONS_SAMPLE,
    CLEAN_SAMPLE,
    UNMATCHED_IGNORE_SAMPLE,
    DUPLICATE_NAMES_SAMPLE,
    ACYCLIC_SIBLINGS_SAMPLE,
):
    if not sample.is_dir():
        raise FileNotFoundError(f"import-linter sample project not found: {sample}")


@pytest.fixture
def duplicate_names_project(tmp_path: Path) -> str:
    """Stage the sample whose two broken contracts share one name.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "duplicate_names"
    shutil.copytree(DUPLICATE_NAMES_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def acyclic_siblings_project(tmp_path: Path) -> str:
    """Stage the sample whose ``acyclic_siblings`` contract finds two cycles.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "acyclic_siblings"
    shutil.copytree(ACYCLIC_SIBLINGS_SAMPLE, dst)
    return str(dst)


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


@pytest.fixture
def unmatched_ignore_project(tmp_path: Path) -> str:
    """Stage the sample whose ``ignore_imports`` entry matches no import.

    Its config sets ``unmatched_ignore_imports_alerting = error``, so
    ``lint-imports`` exits 1 before evaluating any contract (#2371).

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    dst = tmp_path / "unmatched_ignore"
    shutil.copytree(UNMATCHED_IGNORE_SAMPLE, dst)
    return str(dst)


@pytest.fixture
def empty_contract_set_project(tmp_path: Path) -> str:
    """Stage a project shaped like this repo's own dogfood configuration.

    ``pyproject.toml`` carries ``[tool.importlinter]`` with ``root_package``
    and **no** contracts — the configuration #2289 adds to lintro itself.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the staged project root as a string.
    """
    project = tmp_path / "dogfood"
    package = project / "cleanlayered"
    package.mkdir(parents=True)
    shutil.copy(CLEAN_SAMPLE / "cleanlayered" / "__init__.py", package / "__init__.py")
    (project / "pyproject.toml").write_text(
        '[tool.importlinter]\nroot_package = "cleanlayered"\n',
        encoding="utf-8",
    )
    return str(project)
