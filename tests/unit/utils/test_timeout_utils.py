"""Unit tests for timeout utilities."""

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import Any
from unittest.mock import Mock

import pytest
from assertpy import assert_that

from lintro.tools.core.timeout_utils import (
    create_timeout_result,
    get_timeout_value,
    run_subprocess_with_timeout,
)


class MockDefinition:
    """Mock definition for testing."""

    def __init__(self, name: str) -> None:
        """Initialize mock definition.

        Args:
            name: Tool name.
        """
        self.name = name


class MockTool:
    """Mock tool for testing timeout utilities."""

    def __init__(self, name: str = "test_tool", default_timeout: int = 300) -> None:
        """Initialize mock tool.

        Args:
            name: Tool name for testing.
            default_timeout: Default timeout value in seconds.
        """
        self.definition = MockDefinition(name)
        self._default_timeout = default_timeout
        self.options: dict[str, Any] = {}

    def _run_subprocess(
        self,
        cmd: list[str],
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> tuple[bool, str]:
        """Mock subprocess runner.

        Args:
            cmd: Command to run.
            timeout: Optional timeout value.
            cwd: Optional working directory.

        Returns:
            tuple[bool, str]: Success status and output.
        """
        return True, "success"


def test_get_timeout_value_with_option() -> None:
    """Test getting timeout value when set in options."""
    tool = MockTool()
    tool.options["timeout"] = 60

    assert_that(get_timeout_value(tool)).is_equal_to(60)


def test_get_timeout_value_with_default() -> None:
    """Test getting timeout value when using tool default."""
    tool = MockTool(default_timeout=45)

    assert_that(get_timeout_value(tool)).is_equal_to(45)


def test_get_timeout_value_with_custom_default() -> None:
    """Test getting timeout value with custom default parameter."""
    tool = MockTool()

    assert_that(get_timeout_value(tool, 120)).is_equal_to(120)


def test_create_timeout_result() -> None:
    """Test creating a timeout result object."""
    tool = MockTool("pytest")

    result = create_timeout_result(tool, 30, ["pytest", "test"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains(
        "pytest execution timed out (30s limit exceeded)",
    )
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_empty()
    assert_that(result.timed_out).is_true()
    assert_that(result.timeout_seconds).is_equal_to(30)


def test_run_subprocess_with_timeout_success() -> None:
    """Test successful subprocess execution with timeout."""
    tool = MockTool()
    tool._run_subprocess = Mock(return_value=(True, "output"))  # type: ignore[method-assign]

    success, output = run_subprocess_with_timeout(tool, ["echo", "test"])

    assert_that(success).is_true()
    assert_that(output).is_equal_to("output")
    tool._run_subprocess.assert_called_once_with(
        cmd=["echo", "test"],
        timeout=None,
        cwd=None,
    )


def test_run_subprocess_with_timeout_exception() -> None:
    """Test subprocess timeout exception handling."""
    tool = MockTool()

    # Mock subprocess to raise TimeoutExpired
    def mock_run_subprocess(
        cmd: list[str],
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> tuple[bool, str]:
        raise subprocess.TimeoutExpired(
            cmd=["slow", "command"],
            timeout=10,
            output="timeout occurred",
        )

    tool._run_subprocess = mock_run_subprocess  # type: ignore[method-assign]

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_subprocess_with_timeout(tool, ["slow", "command"], timeout=10)

    # Verify the exception has enhanced message
    assert_that(str(exc_info.value.output)).contains("test_tool execution timed out")
    assert_that(str(exc_info.value.output)).contains("(10s limit exceeded)")
