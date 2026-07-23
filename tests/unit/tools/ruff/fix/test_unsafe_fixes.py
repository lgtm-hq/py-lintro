"""Tests for execute_ruff_fix - Unsafe fixes scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.tools.implementations.ruff.fix import execute_ruff_fix


def test_execute_ruff_fix_unsafe_fixes_enabled(
    mock_ruff_tool: MagicMock,
    sample_ruff_json_empty_output: str,
) -> None:
    """Include unsafe fixes when option is enabled.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        sample_ruff_json_empty_output: Sample empty JSON output from ruff.
    """
    mock_ruff_tool.options["unsafe_fixes"] = True

    mock_ruff_tool._run_subprocess.side_effect = [
        (True, sample_ruff_json_empty_output),  # Initial check
        (True, sample_ruff_json_empty_output),  # Fix with unsafe
    ]

    result = execute_ruff_fix(mock_ruff_tool, ["test.py"])

    assert_that(result.success).is_true()


def test_execute_ruff_fix_warns_about_unsafe_fixes(
    mock_ruff_tool: MagicMock,
) -> None:
    """Warn when remaining issues could be fixed with unsafe fixes.

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

    with patch("lintro.tools.implementations.ruff.fix.logger") as mock_logger:
        mock_ruff_tool._run_subprocess.side_effect = [
            (False, remaining_output),  # Initial check
            (False, remaining_output),  # Fix attempt
            (True, "[]"),  # Unsafe fix would fix it
        ]

        execute_ruff_fix(mock_ruff_tool, ["test.py"])

    mock_logger.warning.assert_called()
    warning_msg = str(mock_logger.warning.call_args)
    assert_that(warning_msg).contains("unsafe")
