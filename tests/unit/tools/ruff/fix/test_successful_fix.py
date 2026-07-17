"""Tests for execute_ruff_fix - Successful fix scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_with_fixable_issues(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_output: str,
    sample_ruff_json_empty_output: str,
) -> None:
    """Fix issues and report correct counts when fixable issues exist.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_output: Sample JSON output from ruff with issues.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    # First call: check (finds 2 issues)
    # Second call: fix (returns empty - all fixed)
    mock_ruff_tool._run_subprocess.side_effect = [
        (False, sample_ruff_json_output),  # Initial check finds issues
        (True, sample_ruff_json_empty_output),  # After fix, no remaining
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.output).contains("Fixed 2 issue(s)")


def test_execute_ruff_fix_with_no_issues(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Return no fixes message when no issues exist.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Initial check: no issues
        (True, sample_ruff_json_empty_output),  # Fix: no issues
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to("No fixes applied.")
    assert_that(result.issues_count).is_equal_to(0)


def test_execute_ruff_fix_with_unfixable_issues(
    mock_ruff_tool: MagicMock,
) -> None:
    """Report remaining issues when some cannot be auto-fixed.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    unfixable_output = """[
        {
            "code": "E501",
            "message": "Line too long (120 > 88)",
            "filename": "test.py",
            "location": {"row": 5, "column": 89},
            "end_location": {"row": 5, "column": 120},
            "fix": null
        }
    ]"""

    mock_ruff_tool._run_subprocess.side_effect = [
        (False, unfixable_output),  # Initial check
        (False, unfixable_output),  # After fix attempt (still has issues)
        (False, unfixable_output),  # Unsafe fix check
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.output).contains("cannot be auto-fixed")
