"""Tests for execute_ruff_fix - Format option scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


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
    """Skip format when format option is disabled.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool.options["format"] = False

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Initial check
        (True, sample_ruff_json_empty_output),  # Fix
    ]

    execute_ruff_fix(mock_ruff_tool, ["test.py"])

    # Should only call _run_subprocess twice (check and fix), not format
    assert_that(mock_ruff_tool._run_subprocess.call_count).is_equal_to(2)


def test_execute_ruff_fix_lint_fix_disabled(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Skip lint fix when lint_fix option is disabled.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool.options["lint_fix"] = False
    mock_ruff_tool.options["format"] = False

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Initial check only
    ]

    execute_ruff_fix(mock_ruff_tool, ["test.py"])

    # Should only call initial check, not fix
    assert_that(mock_ruff_tool._run_subprocess.call_count).is_equal_to(1)
