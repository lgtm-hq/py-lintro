"""Tests for execute_ruff_fix - Timeout scenarios."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_timeout_on_initial_check(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return timeout result when initial check times out.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool._run_subprocess.side_effect = subprocess.TimeoutExpired(
        cmd=["ruff", "check"],
        timeout=30,
    )

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")
    assert_that(result.issues_count).is_equal_to(1)


def test_execute_ruff_fix_timeout_on_fix_command(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return timeout result when fix command times out.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    single_issue_output = """[
        {
            "code": "F401",
            "message": "os imported but unused",
            "filename": "test.py",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 1, "column": 10},
            "fix": {"applicability": "safe"}
        }
    ]"""

    mock_ruff_tool._run_subprocess.side_effect = [
        (False, single_issue_output),  # Initial check: 1 issue
        subprocess.TimeoutExpired(cmd=["ruff", "check", "--fix"], timeout=30),
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_execute_ruff_fix_timeout_on_format_check(
    mock_ruff_tool: MagicMock,
) -> None:
    """Return timeout result when format check times out.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    mock_ruff_tool.options["format"] = True

    single_issue_output = """[
        {
            "code": "F401",
            "message": "os imported but unused",
            "filename": "test.py",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 1, "column": 10},
            "fix": {"applicability": "safe"}
        }
    ]"""

    mock_ruff_tool._run_subprocess.side_effect = [
        (False, single_issue_output),  # Initial lint check: 1 issue
        subprocess.TimeoutExpired(cmd=["ruff", "format", "--check"], timeout=30),
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_execute_ruff_fix_timeout_on_format_command(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Return timeout result when format command times out.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool.options["format"] = True

    # Only 1 format issue so validation passes (1 = 0 + 1)
    single_format_issue = "Would reformat: test.py\n"

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Initial lint check: 0 issues
        (False, single_format_issue),  # Format check: 1 file needs formatting
        (True, sample_ruff_json_empty_output),  # Lint fix
        subprocess.TimeoutExpired(cmd=["ruff", "format"], timeout=30),  # Format fix
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_execute_ruff_fix_timeout_on_unsafe_check_continues(
    mock_ruff_tool: MagicMock,
) -> None:
    """Continue execution when unsafe fix check times out.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
    """
    remaining_output = """[
        {
            "code": "F841",
            "message": "Local variable 'x' is assigned but never used",
            "filename": "test.py",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 1, "column": 5},
            "fix": {"applicability": "unsafe"}
        }
    ]"""

    mock_ruff_tool._run_subprocess.side_effect = [
        (False, remaining_output),  # Initial check
        (False, remaining_output),  # Fix attempt
        subprocess.TimeoutExpired(cmd=["ruff"], timeout=30),  # Unsafe check timeout
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    # Should still complete with remaining issues
    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(1)
