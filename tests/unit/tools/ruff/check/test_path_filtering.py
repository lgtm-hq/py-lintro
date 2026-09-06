"""Tests for path handling in execute_ruff_check.

File discovery, exclude patterns, and venv handling are owned by the shared
``BaseToolPlugin.prepare`` pipeline. These tests verify that
``execute_ruff_check`` delegates discovery to that pipeline and consumes the
resulting execution context (relative files and cwd) when building commands.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.ruff.check import execute_ruff_check


def _record_ruff_invocations(
    commands: list[list[str]],
    working_dirs: list[str | None],
) -> Callable[..., tuple[bool, str]]:
    """Build a plain stand-in for the timeout-aware subprocess runner.

    Args:
        commands: List the executed ruff argv is appended to.
        working_dirs: List the working directory of each run is appended to.

    Returns:
        A callable recording each invocation and reporting a clean ruff run.
    """

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record one ruff invocation and report no findings.

        Args:
            **kwargs: Arguments the caller passed to the runner.

        Returns:
            A successful run with empty JSON findings.
        """
        commands.append(list(cast("list[str]", kwargs["cmd"])))
        working_dirs.append(cast("str | None", kwargs.get("cwd")))
        return (True, "[]")

    return fake_run


def test_execute_ruff_check_delegates_discovery_to_prepare_execution(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Run ruff over the context's files, never over the caller's raw path.

    The prepared context lists two files that share no name with the directory
    handed to ``execute_ruff_check``. If the helper walked the filesystem
    itself, the raw path would reach the argv instead.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    prepared_paths: list[list[str]] = []
    context = ruff_execution_context(
        files=["/test/project/discovered_one.py", "/test/project/discovered_two.py"],
        rel_files=["discovered_one.py", "discovered_two.py"],
        cwd="/test/project",
    )

    def fake_prepare(**kwargs: object) -> MagicMock:
        """Record the paths handed to preparation and return a fixed context.

        Args:
            **kwargs: Arguments ``execute_ruff_check`` passed to ``prepare``.

        Returns:
            The context pinning the discovered files for this test.
        """
        prepared_paths.append(list(cast("list[str]", kwargs["paths"])))
        return context

    mock_ruff_tool.prepare.side_effect = fake_prepare
    commands: list[list[str]] = []
    working_dirs: list[str | None] = []

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            side_effect=_record_ruff_invocations(commands, working_dirs),
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    # The caller's raw path is forwarded to preparation, and only the files
    # preparation discovered reach ruff's argv.
    assert_that(prepared_paths).is_equal_to([["/test/project"]])
    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("discovered_one.py", "discovered_two.py")
    assert_that(commands[0]).does_not_contain("/test/project")
    assert_that(result.success).is_true()


def test_execute_ruff_check_converts_paths_to_relative(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Use relative file paths from the execution context for the ruff command.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        files=[
            "/test/project/src/main.py",
            "/test/project/tests/test_main.py",
        ],
        rel_files=["src/main.py", "tests/test_main.py"],
        cwd="/test/project",
    )
    commands: list[list[str]] = []
    working_dirs: list[str | None] = []

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            side_effect=_record_ruff_invocations(commands, working_dirs),
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("src/main.py", "tests/test_main.py")
    assert_that(commands[0]).does_not_contain("/test/project/src/main.py")
    assert_that(working_dirs).is_equal_to(["/test/project"])
    assert_that(result.success).is_true()


def test_execute_ruff_check_handles_multiple_directories(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Handle files from multiple directories.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        files=["/test/project1/main.py", "/test/project2/main.py"],
        rel_files=["project1/main.py", "project2/main.py"],
        cwd="/test",
    )

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
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
    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        files=["/test/project/test.py"],
        rel_files=["/test/project/test.py"],
        cwd=None,
    )
    commands: list[list[str]] = []
    working_dirs: list[str | None] = []

    with (
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            side_effect=_record_ruff_invocations(commands, working_dirs),
        ),
        patch(
            "lintro.tools.ruff.check.parse_ruff_output",
            return_value=[],
        ),
    ):
        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("/test/project/test.py")
    assert_that(working_dirs).is_equal_to([None])
    assert_that(result.success).is_true()
