"""Integration tests for Mypy tool."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.plugins import ToolRegistry

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


@pytest.fixture(autouse=True)
def set_lintro_test_mode_env(lintro_test_mode: object) -> Iterator[None]:
    """Disable config injection for predictable CLI args in tests.

    Args:
        lintro_test_mode: Pytest fixture that enables lintro test mode.

    Yields:
        None: Allows the test to run with modified environment.
    """
    yield


@pytest.fixture
def mypy_tool() -> BaseToolPlugin:
    """Create a mypy tool plugin instance for testing.

    Returns:
        BaseToolPlugin: Configured tool plugin instance for assertions.
    """
    tool = ToolRegistry.get("mypy")
    assert_that(tool).described_as(
        "mypy tool not found in registry",
    ).is_not_none()
    assert tool is not None  # narrow type for mypy
    return tool


@pytest.fixture
def mypy_violation_file() -> Iterator[str]:
    """Copy the mypy_violations.py sample to a temp directory for testing.

    Yields:
        str: Path to the temporary file containing known mypy violations.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    src = (
        repo_root / "test_samples" / "tools" / "python" / "mypy" / "mypy_violations.py"
    )
    if not src.exists():
        pytest.skip(f"Sample file {src} does not exist")
    with tempfile.TemporaryDirectory() as tmpdir:
        dst = os.path.join(tmpdir, "mypy_violations.py")
        shutil.copy(src, dst)
        yield dst


@pytest.fixture
def mypy_clean_file() -> Iterator[str]:
    """Create a temporary clean Python file for mypy.

    Yields:
        str: Path to a temporary Python file without mypy violations.
    """
    content = (
        "from typing import Annotated\n\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        file_path = f.name
    try:
        yield file_path
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(file_path)


def test_mypy_tool_available(mypy_tool: BaseToolPlugin) -> None:
    """Test that mypy tool is registered and available.

    Args:
        mypy_tool: Pytest fixture providing the mypy tool instance.
    """
    assert_that(mypy_tool).is_not_none()
    assert_that(mypy_tool.definition.name).is_equal_to("mypy")


def test_mypy_check_finds_violations(
    mypy_tool: BaseToolPlugin,
    mypy_violation_file: str,
) -> None:
    """Test that mypy check finds type errors in violation file.

    Args:
        mypy_tool: Pytest fixture providing the mypy tool instance.
        mypy_violation_file: Pytest fixture providing file with type errors.
    """
    result = mypy_tool.check([mypy_violation_file], {})

    assert_that(result).is_not_none()
    # Mypy should find type errors in the violations file
    assert_that(
        result.success is False
        or (result.issues is not None and len(result.issues) > 0),
    ).is_true()


def test_mypy_check_clean_file(
    mypy_tool: BaseToolPlugin,
    mypy_clean_file: str,
) -> None:
    """Test that mypy check passes on clean file.

    Args:
        mypy_tool: Pytest fixture providing the mypy tool instance.
        mypy_clean_file: Pytest fixture providing path to clean file.
    """
    result = mypy_tool.check([mypy_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues is None or len(result.issues) == 0).is_true()


def test_mypy_handles_empty_path_list(mypy_tool: BaseToolPlugin) -> None:
    """Test that mypy handles empty path list gracefully.

    Args:
        mypy_tool: Pytest fixture providing the mypy tool instance.
    """
    result = mypy_tool.check([], {})

    # Should complete without crashing
    assert_that(result).is_not_none()
