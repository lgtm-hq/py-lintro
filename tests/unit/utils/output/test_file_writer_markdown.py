"""Unit tests for write_output_file function - Markdown format.

Tests verify Markdown output structure with proper headings, tables, and escaping.
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

    from lintro.models.core.tool_result import ToolResult

    from .conftest import MockIssue


def test_write_markdown_file_creates_valid_structure(
    tmp_path: Path,
    sample_results_empty: list[ToolResult],
) -> None:
    """Verify Markdown file contains proper heading structure and summary table.

    Args:
        tmp_path: Temporary directory path for test output.
        sample_results_empty: Mock tool results with no issues.
    """
    output_path = tmp_path / "report.md"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=sample_results_empty,
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()

    assert_that(content).contains("# Lintro Report")
    assert_that(content).contains("## Summary")
    assert_that(content).contains("| Tool | Issues |")
    assert_that(content).contains("| ruff | 0 |")


def test_write_markdown_file_includes_issue_table(
    tmp_path: Path,
    sample_results_with_issues: list[ToolResult],
) -> None:
    """Verify Markdown output includes issues in table format with proper headers.

    Args:
        tmp_path: Temporary directory path for test output.
        sample_results_with_issues: Mock tool results containing issues.
    """
    output_path = tmp_path / "report.md"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=sample_results_with_issues,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = output_path.read_text()

    assert_that(content).contains("### ruff")
    assert_that(content).contains("| File | Line | Code | Message |")
    assert_that(content).contains("src/main.py")
    assert_that(content).contains("10")
    assert_that(content).contains("E001")


def test_write_markdown_file_escapes_pipe_characters(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., ToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> None:
    """Verify pipe characters in messages are escaped to preserve table formatting.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
        mock_issue_factory: Factory for creating mock issues.
    """
    output_path = tmp_path / "report.md"
    results = [
        mock_tool_result_factory(
            name="ruff",
            issues_count=1,
            issues=[mock_issue_factory(message="A | B")],
        ),
    ]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = output_path.read_text()
    assert_that(content).contains(r"A \| B")
    assert_that(content).does_not_contain("| A | B |")


def test_write_markdown_fix_mode_shows_detected_and_remaining(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., ToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> None:
    """Fix-mode markdown output renders Detected and Remaining tables.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
        mock_issue_factory: Factory for creating mock issues.
    """
    output_path = tmp_path / "report.md"
    results = [
        mock_tool_result_factory(
            name="ruff",
            issues_count=1,
            issues=[mock_issue_factory(code="E501", message="still here")],
            initial_issues=[
                mock_issue_factory(code="F401", message="was fixed"),
                mock_issue_factory(code="E501", message="still here"),
            ],
        ),
    ]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=results,
        action=Action.FIX,
        total_issues=1,
        total_fixed=1,
    )

    content = output_path.read_text()
    assert_that(content).contains("#### Detected issues (2)")
    assert_that(content).contains("#### Remaining issues (1)")
    assert_that(content).contains("F401")
    assert_that(content).contains("E501")


def test_write_markdown_fix_mode_all_fixed(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., ToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> None:
    """When all issues were fixed, shows 'All issues were auto-fixed'.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
        mock_issue_factory: Factory for creating mock issues.
    """
    output_path = tmp_path / "report.md"
    results = [
        mock_tool_result_factory(
            name="ruff",
            issues_count=0,
            issues=[],
            initial_issues=[mock_issue_factory(code="F401", message="was fixed")],
        ),
    ]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=results,
        action=Action.FIX,
        total_issues=0,
        total_fixed=1,
    )

    content = output_path.read_text()
    assert_that(content).contains("#### Detected issues (1)")
    assert_that(content).contains("#### All issues were auto-fixed.")
    assert_that(content).does_not_contain("Remaining issues")
