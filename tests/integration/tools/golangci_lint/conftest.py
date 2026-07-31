"""Pytest configuration for golangci-lint integration tests."""

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
GOLANGCI_SAMPLES = SAMPLE_DIR / "tools" / "go" / "golangci_lint"


@pytest.fixture
def golangci_violation_module(tmp_path: Path) -> str:
    """Copy the golangci-lint violation sample module to a temp directory.

    golangci-lint operates on a Go module, so the whole fixture directory
    (``go.mod``, ``main.go``, ``.golangci.yml``) is copied.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the copied module directory as a string.
    """
    dst = tmp_path / "golangci_violations"
    shutil.copytree(GOLANGCI_SAMPLES, dst)
    return str(dst)


@pytest.fixture
def golangci_clean_module(tmp_path: Path) -> str:
    """Create a minimal, violation-free Go module in a temp directory.

    Mirrors the sample module's linter configuration (errcheck, ineffassign)
    but with source that triggers neither, so golangci-lint reports no issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created module directory as a string.
    """
    dst = tmp_path / "golangci_clean"
    dst.mkdir()
    (dst / "go.mod").write_text(
        "module example.com/golangciclean\n\ngo 1.24.9\n",
    )
    (dst / ".golangci.yml").write_text(
        'version: "2"\nlinters:\n  enable:\n    - errcheck\n    - ineffassign\n',
    )
    (dst / "main.go").write_text(
        'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("ok")\n}\n',
    )
    return str(dst)
