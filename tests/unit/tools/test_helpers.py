"""Shared test utilities for tool plugin tests.

This module provides helper functions and utilities that complement
the fixtures in conftest.py. These helpers are designed for reuse
across multiple tool test modules.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.models.core.tool_result import ToolResult

if TYPE_CHECKING:
    from collections.abc import Generator

    from lintro.plugins.base import BaseToolPlugin


# =============================================================================
# Sample dataclasses for test data
# =============================================================================


@dataclass
class SampleIssue:
    """Sample issue for testing tool output parsing.

    This provides a simple, reusable issue structure that can be
    customized for different test scenarios.

    Attributes:
        file: The file path where the issue was found.
        line: The line number of the issue.
        column: The column number of the issue.
        code: The issue code/identifier.
        message: The issue description.
        severity: The severity level of the issue.
    """

    file: str = "src/main.py"
    line: int = 10
    column: int = 1
    code: str = "E001"
    message: str = "Test error message"
    severity: str = "error"


@dataclass
class SampleToolConfig:
    """Sample tool configuration for testing.

    Provides default values that can be overridden for specific test cases.

    Attributes:
        priority: Execution priority for the tool.
        file_patterns: Glob patterns for matching files.
        timeout: Execution timeout in seconds.
        options: Additional tool-specific options.
    """

    priority: int = 50
    file_patterns: list[str] = field(default_factory=lambda: ["*.py"])
    timeout: int = 30
    options: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Assertion helpers
# =============================================================================


def assert_tool_result_success(
    result: ToolResult,
    expected_name: ToolName | str | None = None,
    expected_issues_count: int = 0,
) -> None:
    """Assert a tool result indicates success.

    Args:
        result: The ToolResult to verify.
        expected_name: Expected tool name (optional).
        expected_issues_count: Expected issue count (default 0).

    Example:
        result = plugin.check(files, options)
        assert_tool_result_success(result, ToolName.RUFF)
    """
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(expected_issues_count)

    if expected_name is not None:
        assert_that(result.name).is_equal_to(expected_name)


def assert_tool_result_failure(
    result: ToolResult,
    expected_name: ToolName | str | None = None,
    min_issues: int = 1,
) -> None:
    """Assert a tool result indicates failure with issues.

    Args:
        result: The ToolResult to verify.
        expected_name: Expected tool name (optional).
        min_issues: Minimum expected issue count (default 1).

    Example:
        result = plugin.check(files, options)
        assert_tool_result_failure(result, ToolName.RUFF, min_issues=3)
    """
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(min_issues)

    if expected_name is not None:
        assert_that(result.name).is_equal_to(expected_name)


def assert_tool_result_timeout(
    result: ToolResult,
    expected_name: ToolName | str | None = None,
) -> None:
    """Assert a tool result indicates a timeout occurred.

    Args:
        result: The ToolResult to verify.
        expected_name: Expected tool name (optional).

    Example:
        result = plugin.check(files, options)
        assert_tool_result_timeout(result, ToolName.RUFF)
    """
    assert_that(result.success).is_false()
    assert_that(result.output).is_not_none()
    assert_that(result.output.lower() if result.output else "").contains("timeout")

    if expected_name is not None:
        assert_that(result.name).is_equal_to(expected_name)


def assert_tool_result_skipped(
    result: ToolResult,
    expected_name: ToolName | str | None = None,
) -> None:
    """Assert a tool result indicates the check was skipped.

    Args:
        result: The ToolResult to verify.
        expected_name: Expected tool name (optional).

    Example:
        result = plugin.check(files, options)
        assert_tool_result_skipped(result, ToolName.RUFF)
    """
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)

    if expected_name is not None:
        assert_that(result.name).is_equal_to(expected_name)


# =============================================================================
# Mock creation helpers
# =============================================================================


def create_mock_subprocess_result(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock subprocess result with specified values.

    Args:
        stdout: Standard output content.
        stderr: Standard error content.
        returncode: Process return code.

    Returns:
        A MagicMock configured as a subprocess result.

    Example:
        mock_result = create_mock_subprocess_result(
            stdout="All checks passed",
            returncode=0,
        )
    """
    mock_result = MagicMock()
    mock_result.stdout = stdout
    mock_result.stderr = stderr
    mock_result.returncode = returncode
    return mock_result


def create_mock_tool_result(
    name: ToolName | str = ToolName.RUFF,
    success: bool = True,
    issues_count: int = 0,
    output: str = "",
    issues: list[Any] | None = None,
) -> MagicMock:
    """Create a mock ToolResult with specified values.

    Args:
        name: Tool name.
        success: Whether the check succeeded.
        issues_count: Number of issues found.
        output: Tool output string.
        issues: List of parsed issues.

    Returns:
        A MagicMock configured as a ToolResult.

    Example:
        mock_result = create_mock_tool_result(
            name=ToolName.MYPY,
            success=False,
            issues_count=5,
        )
    """
    result = MagicMock()
    result.name = name
    result.success = success
    result.issues_count = issues_count
    result.output = output
    result.issues = issues if issues is not None else []
    return result


# =============================================================================
# Context managers for patching
# =============================================================================


@contextmanager
def patch_plugin_for_check_test(
    plugin: BaseToolPlugin,
    subprocess_result: tuple[bool, str],
    files: list[str] | None = None,
    timeout: int = 30,
    cwd: str = "/test/project",
) -> Generator[MagicMock, None, None]:
    """Context manager for patching a plugin to test the check method.

    This helper patches _prepare_execution and _run_subprocess to allow
    testing the check method without actual subprocess calls.

    Args:
        plugin: The plugin instance to patch.
        subprocess_result: Tuple of (success, output) for subprocess mock.
        files: List of file paths (optional).
        timeout: Timeout value for execution context.
        cwd: Working directory for execution context.

    Yields:
        MagicMock: The mock execution context.

    Example:
        with patch_plugin_for_check_test(ruff_plugin, (True, "")) as ctx:
            result = ruff_plugin.check(["test.py"], {})
            assert_that(result.success).is_true()
    """
    from lintro.plugins.base import ExecutionContext

    mock_ctx = MagicMock(spec=ExecutionContext)
    mock_ctx.files = files if files is not None else ["test.py"]
    mock_ctx.rel_files = files if files is not None else ["test.py"]
    mock_ctx.cwd = cwd
    mock_ctx.timeout = timeout
    mock_ctx.should_skip = False
    mock_ctx.early_result = None

    with (
        patch.object(plugin, "_prepare_execution", return_value=mock_ctx),
        patch.object(plugin, "_run_subprocess", return_value=subprocess_result),
    ):
        yield mock_ctx


@contextmanager
def patch_plugin_for_fix_test(
    plugin: BaseToolPlugin,
    subprocess_result: tuple[bool, str],
    files: list[str] | None = None,
    timeout: int = 30,
    cwd: str = "/test/project",
) -> Generator[MagicMock, None, None]:
    """Context manager for patching a plugin to test the fix method.

    Similar to patch_plugin_for_check_test but configured for fix operations.

    Args:
        plugin: The plugin instance to patch.
        subprocess_result: Tuple of (success, output) for subprocess mock.
        files: List of file paths (optional).
        timeout: Timeout value for execution context.
        cwd: Working directory for execution context.

    Yields:
        MagicMock: The mock execution context.

    Example:
        with patch_plugin_for_fix_test(ruff_plugin, (True, "1 fixed")) as ctx:
            result = ruff_plugin.fix(["test.py"], {})
            assert_that(result.success).is_true()
    """
    from lintro.plugins.base import ExecutionContext

    mock_ctx = MagicMock(spec=ExecutionContext)
    mock_ctx.files = files if files is not None else ["test.py"]
    mock_ctx.rel_files = files if files is not None else ["test.py"]
    mock_ctx.cwd = cwd
    mock_ctx.timeout = timeout
    mock_ctx.should_skip = False
    mock_ctx.early_result = None

    with (
        patch.object(plugin, "_prepare_execution", return_value=mock_ctx),
        patch.object(plugin, "_run_subprocess", return_value=subprocess_result),
    ):
        yield mock_ctx
