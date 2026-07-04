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
    from lintro.models.core.tool_result import ToolResult


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
