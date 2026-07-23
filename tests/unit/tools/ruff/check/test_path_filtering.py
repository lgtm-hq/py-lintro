"""Tests for path handling in execute_ruff_check.

File discovery, exclude patterns, and venv handling are owned by the shared
``BaseToolPlugin._prepare_execution`` pipeline. These tests verify that
``execute_ruff_check`` delegates discovery to that pipeline and consumes the
resulting execution context (relative files and cwd) when building commands.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_delegates_discovery_to_prepare_execution(
    mock_ruff_tool: MagicMock,
) -> None:
    """Delegate file discovery to the shared preparation pipeline.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        mock_ruff_tool._prepare_execution.assert_called_once()
        call = mock_ruff_tool._prepare_execution.call_args
        assert_that(call.kwargs.get("paths")).is_equal_to(["/test/project"])


def test_execute_ruff_check_converts_paths_to_relative(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Use relative file paths from the execution context for the ruff command.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        files=[
            "/test/project/src/main.py",
            "/test/project/tests/test_main.py",
        ],
        rel_files=["src/main.py", "tests/test_main.py"],
        cwd="/test/project",
    )

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.commands.build_ruff_check_command",
        ) as mock_build_cmd,
    ):
        mock_build_cmd.return_value = [
            "ruff",
            "check",
            "src/main.py",
            "tests/test_main.py",
        ]

        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        call_args = mock_build_cmd.call_args
        files_arg = call_args.kwargs.get("files") or call_args.args[1]
        # Files should be the relative paths from the execution context
        assert_that(files_arg).contains("src/main.py")
        assert_that(files_arg).contains("tests/test_main.py")


def test_execute_ruff_check_handles_multiple_directories(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Handle files from multiple directories.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        files=["/test/project1/main.py", "/test/project2/main.py"],
        rel_files=["project1/main.py", "project2/main.py"],
        cwd="/test",
    )

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test"])

        assert_that(result.success).is_true()


def test_execute_ruff_check_uses_absolute_paths_when_no_cwd(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Use absolute paths when the context has no common working directory.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        files=["/test/project/test.py"],
        rel_files=["/test/project/test.py"],
        cwd=None,
    )

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.commands.build_ruff_check_command",
        ) as mock_build_cmd,
    ):
        mock_build_cmd.return_value = ["ruff", "check", "/test/project/test.py"]

        execute_ruff_check(mock_ruff_tool, ["/test/project"])

        call_args = mock_build_cmd.call_args
        files_arg = call_args.kwargs.get("files") or call_args.args[1]
        # Should use absolute path
        assert_that(files_arg[0]).starts_with("/")
