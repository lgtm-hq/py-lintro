"""Unit tests for write_output_file function - CSV format.

Tests verify CSV output structure with proper headers and data rows.
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


def test_write_csv_file_creates_valid_file_with_headers(
    tmp_path: Path,
    sample_results_empty: list[ToolResult],
) -> None:
    """Verify CSV file contains proper header row with all required columns.

    Args:
        tmp_path: Temporary directory path for test output.
        sample_results_empty: Mock tool results with no issues.
    """
    output_path = tmp_path / "report.csv"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.CSV,
        all_results=sample_results_empty,
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()
    lines = content.strip().split("\n")

    assert_that(lines).is_not_empty()
    header = lines[0]
    assert_that(header).contains("tool")
    assert_that(header).contains("issues_count")
    assert_that(header).contains("file")
    assert_that(header).contains("line")
    assert_that(header).contains("code")
    assert_that(header).contains("message")


def test_write_csv_file_includes_issue_data(
    tmp_path: Path,
    sample_results_with_issues: list[ToolResult],
) -> None:
    """Verify CSV output includes issue details in data rows.

    Args:
        tmp_path: Temporary directory path for test output.
        sample_results_with_issues: Mock tool results containing issues.
    """
    output_path = tmp_path / "report.csv"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.CSV,
        all_results=sample_results_with_issues,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = output_path.read_text()
    lines = content.strip().split("\n")

    assert_that(lines).is_length(2)  # header + 1 data row
    assert_that(content).contains("src/main.py")
    assert_that(content).contains("E001")
    assert_that(content).contains("ruff")


def test_write_csv_uses_merged_issue_count_not_stale_value(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., ToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> None:
    """CSV issues_count reflects merged/deduped count, not stale ToolResult value.

    Regression test for #856: fix-mode results carry a pre-merge
    ``issues_count`` that can diverge from ``len(merged_issues)``. The CSV
    writer must report the merged count so it agrees with the JSON writer.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating ToolResult objects.
        mock_issue_factory: Factory for creating MockIssue objects.
    """
    detected = mock_issue_factory(code="E001")
    remaining = mock_issue_factory(code="E002")
    result = mock_tool_result_factory(
        name="ruff",
        # Deliberately stale/wrong pre-merge count.
        issues_count=99,
        issues=[remaining],
        initial_issues=[detected, remaining],
        initial_issues_count=2,
        fixed_issues_count=1,
        remaining_issues_count=1,
    )

    output_path = tmp_path / "report.csv"
    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.CSV,
        all_results=[result],
        action=Action.FIX,
        total_issues=2,
        total_fixed=1,
    )

    content = output_path.read_text()
    lines = content.strip().split("\n")
    # Merged/deduped count is 2 (detected [E001, E002] + remaining [E002]).
    assert_that(content).does_not_contain("99")
    assert_that(lines).is_length(3)  # header + 2 merged issue rows
    assert_that(content).contains("E001")
    assert_that(content).contains("E002")
    for data_row in lines[1:]:
        assert_that(data_row.split(",")[1]).is_equal_to("2")
