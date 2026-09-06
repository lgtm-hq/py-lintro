"""Tests for execute_ruff_fix - Format option scenarios."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def _record_ruff_commands(
    *,
    tool: MagicMock,
    output: str,
) -> list[list[str]]:
    """Make the fake tool record every ruff argv it is asked to run.

    Args:
        tool: Mock RuffTool whose ``_run_subprocess`` is replaced.
        output: Stdout each recorded invocation reports back.

    Returns:
        The list that accumulates one argv per ruff invocation.
    """
    commands: list[list[str]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the ruff argv and report a clean run.

        Args:
            **kwargs: Arguments ruff passed to ``_run_subprocess``.

        Returns:
            A successful run reporting ``output``.
        """
        commands.append(list(cast("list[str]", kwargs["cmd"])))
        return (True, output)

    tool._run_subprocess.side_effect = fake_run
    return commands


def test_execute_ruff_fix_with_format_enabled(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
    sample_ruff_format_check_output: str,
) -> None:
    """Run format when format option is enabled.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
        sample_ruff_format_check_output: Sample format check output from ruff.
    """
    mock_ruff_tool.options["format"] = True

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Initial lint check
        (False, sample_ruff_format_check_output),  # Format check (2 files)
        (True, sample_ruff_json_empty_output),  # Lint fix
        (True, ""),  # Format fix
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_true()
    # 2 format issues were found and fixed
    assert_that(result.fixed_issues_count).is_equal_to(2)


def test_execute_ruff_fix_format_disabled(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """No ruff ``format`` command is issued when the format option is off.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool.options["format"] = False
    commands = _record_ruff_commands(
        tool=mock_ruff_tool,
        output=sample_ruff_json_empty_output,
    )

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    subcommands = [command[1] for command in commands]
    assert_that(subcommands).does_not_contain("format")
    assert_that(subcommands).contains("check")
    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_equal_to(0)


def test_execute_ruff_fix_lint_fix_disabled(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """No ruff invocation carries ``--fix`` when lint fixing is off.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool.options["lint_fix"] = False
    mock_ruff_tool.options["format"] = False
    commands = _record_ruff_commands(
        tool=mock_ruff_tool,
        output=sample_ruff_json_empty_output,
    )

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(commands).is_not_empty()
    for command in commands:
        assert_that(command).does_not_contain("--fix")
    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_equal_to(0)
