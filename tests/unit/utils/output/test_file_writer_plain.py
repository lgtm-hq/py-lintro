"""Unit tests for write_output_file function - Plain/Grid format.

Tests verify plain text output structure with proper headers and totals.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.utils.output.file_writer import write_output_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from .conftest import MockIssue, MockToolResult


def test_write_plain_file_creates_valid_structure(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., MockToolResult],
) -> None:
    """Verify plain text file contains report header and summary totals.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
    """
    output_path = tmp_path / "report.txt"
    results = [mock_tool_result_factory(name="ruff", issues_count=5, output="5 issues")]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.PLAIN,
        all_results=results,  # type: ignore[arg-type]
        action=Action.CHECK,
        total_issues=5,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()

    assert_that(content).contains("Lintro Check Report")
    assert_that(content).contains("=" * 40)
    assert_that(content).contains("ruff: 5 issues")
    assert_that(content).contains("Total Issues: 5")


def test_write_plain_file_shows_fixed_count_for_fix_action(
    tmp_path: Path,
    sample_results_empty: list[MockToolResult],
) -> None:
    """Verify fix action report includes 'Total Fixed' instead of just issues.

    Args:
        tmp_path: Temporary directory path for test output.
        sample_results_empty: Mock tool results with no issues.
    """
    output_path = tmp_path / "report.txt"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.PLAIN,
        all_results=sample_results_empty,  # type: ignore[arg-type]
        action=Action.FIX,
        total_issues=0,
        total_fixed=5,
    )

    content = output_path.read_text()

    assert_that(content).contains("Lintro Fix Report")
    assert_that(content).contains("Total Fixed: 5")


def test_write_plain_fix_mode_shows_detected_and_remaining(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., MockToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> None:
    """Fix-mode plain output renders Detected and Remaining tables.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
        mock_issue_factory: Factory for creating mock issues.
    """
    from lintro.parsers.ruff.ruff_issue import RuffIssue

    output_path = tmp_path / "report.txt"
    results = [
        mock_tool_result_factory(
            name="ruff",
            issues_count=1,
            issues=[
                RuffIssue(
                    file="a.py",
                    line=1,
                    column=1,
                    code="E501",
                    message="still here",
                ),
            ],
            initial_issues=[
                RuffIssue(
                    file="a.py",
                    line=1,
                    column=1,
                    code="F401",
                    message="was fixed",
                ),
                RuffIssue(
                    file="a.py",
                    line=1,
                    column=1,
                    code="E501",
                    message="still here",
                ),
            ],
        ),
    ]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.PLAIN,
        all_results=results,  # type: ignore[arg-type]
        action=Action.FIX,
        total_issues=1,
        total_fixed=1,
    )

    content = output_path.read_text()
    assert_that(content).contains("Detected issues (2)")
    assert_that(content).contains("Remaining issues (1)")
