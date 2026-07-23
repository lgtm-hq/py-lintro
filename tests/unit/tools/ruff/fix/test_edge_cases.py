"""Tests for execute_ruff_fix - Edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_handles_malformed_json(
    mock_ruff_tool: MagicMock,
) -> None:
    """Handle malformed JSON output gracefully.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool._run_subprocess.side_effect = [
        (True, "not valid json"),  # Initial check with bad output
        (True, "[]"),  # Fix succeeds
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    # Should not crash, parser handles malformed JSON
    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("ruff")


def test_execute_ruff_fix_handles_empty_output(
    mock_ruff_tool: MagicMock,
) -> None:
    """Handle empty output from subprocess.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool._run_subprocess.side_effect = [
        (True, ""),  # Empty output
        (True, ""),
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_fix_format_exit_code_handling(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
    sample_ruff_format_check_output: str,
) -> None:
    """Handle ruff format exit code 1 as success when files are formatted.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
        sample_ruff_format_check_output: Sample format check output from ruff.
    """
    mock_ruff_tool.options["format"] = True

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Lint check
        (False, sample_ruff_format_check_output),  # Format check (exit 1)
        (True, sample_ruff_json_empty_output),  # Lint fix
        (False, ""),  # Format fix (exit 1 means files were formatted)
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    # Format with exit code 1 and initial format issues should still be success
    assert_that(result.success).is_true()


def test_execute_ruff_fix_multiple_files(
    mock_ruff_tool: MagicMock,
    temp_python_files: list[str],
    sample_ruff_json_empty_output: str,
) -> None:
    """Handle multiple files correctly.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        temp_python_files: List of temporary Python files for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),
        (True, sample_ruff_json_empty_output),
    ]

    result = execute_ruff_fix(mock_ruff_tool, temp_python_files)

    assert_that(result.success).is_true()
