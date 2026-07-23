"""Tests for format check normalization in execute_ruff_check."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.ruff.ruff_format_issue import RuffFormatIssue
from lintro.tools.implementations.ruff.check import execute_ruff_check


def test_execute_ruff_check_normalizes_format_paths_to_absolute(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Normalize format check file paths to absolute paths.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool.options["format_check"] = True
    mock_ruff_tool._prepare_execution.return_value = ruff_execution_context(
        files=["/test/project/test.py"],
        rel_files=["test.py"],
        cwd="/test/project",
    )

    with (
        patch(
            "lintro.tools.implementations.ruff.check.run_subprocess_with_timeout",
        ) as mock_subprocess,
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_output",
            return_value=[],
        ),
        patch(
            "lintro.tools.implementations.ruff.check.parse_ruff_format_check_output",
            return_value=["test.py"],  # Relative path from format check
        ),
        patch("os.path.isabs", return_value=False),
        patch("os.path.abspath", return_value="/test/project/test.py"),
    ):
        mock_subprocess.side_effect = [
            (True, "[]"),
            (False, "Would reformat: test.py"),
        ]

        result = execute_ruff_check(mock_ruff_tool, ["/test/project"])

        # Format issues should have normalized absolute paths
        assert_that(result.issues_count).is_equal_to(1)
        assert_that(result.issues).is_not_none()
        format_issue = cast(RuffFormatIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(format_issue.file).contains("/test/project")
