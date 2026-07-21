"""Tests for execute_ruff_fix - Mixed lint and format issues."""

from __future__ import annotations

from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_combined_lint_and_format_issues(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_output: str,
    sample_ruff_format_check_output: str,
) -> None:
    """Handle both lint and format issues correctly.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_output: Sample JSON output from ruff.
        sample_ruff_format_check_output: Sample format check output from ruff.
    """
    mock_ruff_tool.options["format"] = True

    mock_ruff_tool._run_subprocess.side_effect = [
        (False, sample_ruff_json_output),  # Initial lint: 2 issues
        (False, sample_ruff_format_check_output),  # Format check: 2 files
        (True, "[]"),  # Lint fix: all fixed
        (True, ""),  # Format fix: success
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_true()
    # 2 lint + 2 format = 4 total initial
    assert_that(result.initial_issues_count).is_equal_to(4)
    # All fixed
    assert_that(result.fixed_issues_count).is_equal_to(4)


def test_execute_ruff_fix_partial_fix_with_format(
    mock_ruff_tool: MagicMock,
    sample_ruff_format_check_output: str,
) -> None:
    """Report partial fix when some lint issues remain after format.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_format_check_output: Sample format check output from ruff.
    """
    mock_ruff_tool.options["format"] = True

    lint_with_unfixable = """[
        {
            "code": "E501",
            "message": "Line too long",
            "filename": "test.py",
            "location": {"row": 1, "column": 89},
            "end_location": {"row": 1, "column": 120},
            "fix": null
        }
    ]"""

    mock_ruff_tool._run_subprocess.side_effect = [
        (False, lint_with_unfixable),  # Initial lint: 1 unfixable
        (False, sample_ruff_format_check_output),  # Format check: 2 files
        (False, lint_with_unfixable),  # Lint fix: still 1 remaining
        (False, lint_with_unfixable),  # Unsafe check
        (True, ""),  # Format fix
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_false()
    # 1 lint + 2 format initial
    assert_that(result.initial_issues_count).is_equal_to(3)
    # 0 lint fixed + 2 format fixed
    assert_that(result.fixed_issues_count).is_equal_to(2)
    # 1 remaining unfixable
    assert_that(result.remaining_issues_count).is_equal_to(1)
