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


def _timeout_tool(default_timeout: int = 300, option_timeout: int | None = 30) -> Any:
    """Build a mock tool whose subprocess always times out.

    Args:
        default_timeout: Value for ``tool._default_timeout``.
        option_timeout: Value stored under ``tool.options['timeout']``; omitted
            when None so the option key is absent.

    Returns:
        Any: Configured MockTool that raises TimeoutExpired on subprocess runs.
    """
    tool = MockTool(default_timeout=default_timeout)
    if option_timeout is not None:
        tool.options["timeout"] = option_timeout

    def _raise(
        cmd: list[str],
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> tuple[bool, str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 0)

    tool._run_subprocess = _raise  # type: ignore[method-assign]
    return tool


def test_run_subprocess_timeout_zero_preserved() -> None:
    """An explicit timeout=0 is preserved, not replaced by the option/default.

    Regression test for the falsy-zero bug (#1221): ``timeout or fallback``
    treated ``0`` as missing and reported the configured timeout instead.
    """
    tool = _timeout_tool(default_timeout=300, option_timeout=30)

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_subprocess_with_timeout(tool, ["slow"], timeout=0)

    # The reported timeout must be the caller-provided 0, not the 30s option.
    assert_that(exc_info.value.timeout).is_equal_to(0)
    assert_that(str(exc_info.value.output)).contains("(0s limit exceeded)")
    assert_that(str(exc_info.value.output)).does_not_contain("(30s limit exceeded)")


def test_run_subprocess_timeout_none_uses_option_fallback() -> None:
    """A timeout of None falls back to the tool option, then the default."""
    tool = _timeout_tool(default_timeout=300, option_timeout=45)

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_subprocess_with_timeout(tool, ["slow"], timeout=None)

    assert_that(exc_info.value.timeout).is_equal_to(45)
    assert_that(str(exc_info.value.output)).contains("(45s limit exceeded)")


def test_run_subprocess_timeout_none_uses_default_when_no_option() -> None:
    """A timeout of None falls back to the tool default when no option is set."""
    tool = _timeout_tool(default_timeout=300, option_timeout=None)

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_subprocess_with_timeout(tool, ["slow"], timeout=None)

    assert_that(exc_info.value.timeout).is_equal_to(300)
    assert_that(str(exc_info.value.output)).contains("(300s limit exceeded)")


def test_run_subprocess_timeout_positive_passthrough() -> None:
    """An explicit positive timeout is passed through unchanged."""
    tool = _timeout_tool(default_timeout=300, option_timeout=30)

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_subprocess_with_timeout(tool, ["slow"], timeout=45)

    assert_that(exc_info.value.timeout).is_equal_to(45)
    assert_that(str(exc_info.value.output)).contains("(45s limit exceeded)")
