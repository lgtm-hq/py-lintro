"""Unit tests for output_writers module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.tool_result import ToolResult
from lintro.utils.output import sanitize_csv_value, write_output_file

# --- sanitize_csv_value tests ---


@pytest.mark.parametrize(
    "input_value,expected",
    [
        ("normal text", "normal text"),
        ("", ""),
        ("=SUM(A1:A10)", "'=SUM(A1:A10)"),
        ("+1234", "'+1234"),
        ("-500", "'-500"),
        ("@mention", "'@mention"),
        ("A normal value", "A normal value"),
        ("test=value", "test=value"),
    ],
    ids=["normal", "empty", "equals", "plus", "minus", "at", "normal_space", "mid_eq"],
)
def test_sanitize_csv_value(input_value: str, expected: str) -> None:
    """Test CSV value sanitization for formula injection prevention.

    Args:
        input_value: The input value to sanitize.
        expected: The expected sanitized output.
    """
    result = sanitize_csv_value(input_value)
    assert_that(result).is_equal_to(expected)


# --- write_output_file tests ---


@pytest.fixture
def sample_results() -> list[ToolResult]:
    """Create sample ToolResult objects for testing.

    Returns:
        List of sample ToolResult objects.
    """
    mock_issue = MagicMock()
    mock_issue.file = "test.py"
    mock_issue.line = 10
    mock_issue.code = "E001"
    mock_issue.message = "Test error message"
    mock_issue.doc_url = ""

    result_with_issues = ToolResult(
        name="ruff",
        success=False,
        output="Found issues",
        issues_count=1,
        issues=[mock_issue],
    )
    result_no_issues = ToolResult(
        name="mypy",
        success=True,
        output="No issues",
        issues_count=0,
    )
    return [result_with_issues, result_no_issues]


def test_write_json_output(tmp_path: Path, sample_results: list[ToolResult]) -> None:
    """Test writing JSON output format.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.json"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = json.loads(output_path.read_text())
    assert_that(content).contains_key("timestamp")
    assert_that(content["action"]).is_equal_to("check")
    assert_that(content["summary"]["total_issues"]).is_equal_to(1)
    assert_that(content["summary"]["tools_run"]).is_equal_to(2)
    assert_that(len(content["results"])).is_equal_to(2)


def test_write_csv_output(tmp_path: Path, sample_results: list[ToolResult]) -> None:
    """Test writing CSV output format.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.csv"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.CSV,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()
    assert_that(content).contains("tool,issues_count,file,line,code,message")
    assert_that(content).contains("ruff")
    assert_that(content).contains("test.py")


def test_write_markdown_output(
    tmp_path: Path,
    sample_results: list[ToolResult],
) -> None:
    """Test writing Markdown output format.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.md"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()
    assert_that(content).contains("# Lintro Report")
    assert_that(content).contains("## Summary")
    assert_that(content).contains("| Tool | Issues |")
    assert_that(content).contains("| ruff | 1 |")
    assert_that(content).contains("### ruff (1 issues)")
    assert_that(content).contains("No issues found.")


def test_write_html_output(tmp_path: Path, sample_results: list[ToolResult]) -> None:
    """Test writing HTML output format.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.html"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.HTML,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()
    assert_that(content).contains("<html>")
    assert_that(content).contains("<h1>Lintro Report</h1>")
    assert_that(content).contains("<th>Tool</th>")
    assert_that(content).contains("<td>ruff</td>")
    assert_that(content).contains("</html>")


def test_write_plain_output(tmp_path: Path, sample_results: list[ToolResult]) -> None:
    """Test writing plain text output format.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.txt"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.PLAIN,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = output_path.read_text()
    assert_that(content).contains("Lintro Check Report")
    assert_that(content).contains("ruff: 1 issues")
    assert_that(content).contains("Total Issues: 1")


def test_write_plain_output_fix_action(
    tmp_path: Path,
    sample_results: list[ToolResult],
) -> None:
    """Test plain output includes fixed count for fix action.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.txt"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.PLAIN,
        all_results=sample_results,
        action=Action.FIX,
        total_issues=1,
        total_fixed=1,
    )

    content = output_path.read_text()
    assert_that(content).contains("Lintro Fix Report")
    assert_that(content).contains("Total Fixed: 1")


def test_creates_parent_directories(
    tmp_path: Path,
    sample_results: list[ToolResult],
) -> None:
    """Test that parent directories are created if they don't exist.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "nested" / "dir" / "report.json"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()


def test_json_with_issues_details(tmp_path: Path) -> None:
    """Test JSON output includes issue details.

    Args:
        tmp_path: Temporary directory path for testing.
    """
    mock_issue = MagicMock()
    mock_issue.file = "error.py"
    mock_issue.line = 42
    mock_issue.code = "W999"
    mock_issue.message = "Warning message"
    mock_issue.doc_url = ""

    result = ToolResult(
        name="pylint",
        success=False,
        output="Issues found",
        issues_count=1,
        issues=[mock_issue],
    )

    output_path = tmp_path / "report.json"
    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = json.loads(output_path.read_text())
    issues = content["results"][0]["issues"]
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0]["file"]).is_equal_to("error.py")
    assert_that(issues[0]["line"]).is_equal_to(42)
    assert_that(issues[0]["code"]).is_equal_to("W999")


def test_html_escapes_special_characters(tmp_path: Path) -> None:
    """Test HTML output escapes special characters.

    Args:
        tmp_path: Temporary directory path for testing.
    """
    mock_issue = MagicMock()
    mock_issue.file = "test.py"
    mock_issue.line = 1
    mock_issue.code = "E001"
    mock_issue.message = "<script>alert('xss')</script>"
    mock_issue.doc_url = ""

    result = ToolResult(
        name="<tool>",
        success=False,
        output="",
        issues_count=1,
        issues=[mock_issue],
    )

    output_path = tmp_path / "report.html"
    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.HTML,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = output_path.read_text()
    assert_that(content).contains("&lt;tool&gt;")
    assert_that(content).contains("&lt;script&gt;")
    assert_that(content).does_not_contain("<script>alert")


def test_markdown_escapes_pipe_characters(tmp_path: Path) -> None:
    """Test Markdown output escapes pipe characters.

    Args:
        tmp_path: Temporary directory path for testing.
    """
    mock_issue = MagicMock()
    mock_issue.file = "test|file.py"
    mock_issue.line = 1
    mock_issue.code = "E|001"
    mock_issue.message = "Message with | pipe"
    mock_issue.doc_url = ""

    result = ToolResult(
        name="ruff",
        success=False,
        output="",
        issues_count=1,
        issues=[mock_issue],
    )

    output_path = tmp_path / "report.md"
    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = output_path.read_text()
    assert_that(content).contains(r"test\|file.py")
    assert_that(content).contains(r"E\|001")


def test_grid_format_same_as_plain(
    tmp_path: Path,
    sample_results: list[ToolResult],
) -> None:
    """Test GRID format uses same output as PLAIN.

    Args:
        tmp_path: Temporary directory path for testing.
        sample_results: Sample tool results for testing.
    """
    output_path = tmp_path / "report.txt"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.GRID,
        all_results=sample_results,
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = output_path.read_text()
    assert_that(content).contains("Lintro Check Report")
