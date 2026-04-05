"""Unit tests for write_output_file function - JSON format.

Tests verify JSON output structure, metadata, and issue serialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.utils.output.file_writer import write_output_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from .conftest import MockIssue, MockToolResult


def test_write_json_file_creates_valid_file(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., MockToolResult],
) -> None:
    """Verify JSON file is created with correct structure and metadata.

    The output should contain action, summary with totals, and results array.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
    """
    output_path = tmp_path / "report.json"
    results = [
        mock_tool_result_factory(name="ruff", issues_count=2, output="found issues"),
    ]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=results,  # type: ignore[arg-type]
        action=Action.CHECK,
        total_issues=2,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    content = json.loads(output_path.read_text())

    assert_that(content).is_instance_of(dict)
    assert_that(content["action"]).is_equal_to("check")
    assert_that(content["summary"]["total_issues"]).is_equal_to(2)
    assert_that(content["summary"]["tools_run"]).is_equal_to(1)
    assert_that(content["results"]).is_length(1)
    assert_that(content["results"][0]["tool"]).is_equal_to("ruff")
    assert_that(content["timestamp"]).is_not_none()


def test_write_json_file_includes_parsed_issues(
    tmp_path: Path,
    sample_results_with_issues: list[MockToolResult],
) -> None:
    """Verify parsed issues are properly serialized in JSON output with all fields.

    Each issue should contain file, line, code, and message attributes.

    Args:
        tmp_path: Temporary directory path for test output.
        sample_results_with_issues: Mock tool results containing issues.
    """
    output_path = tmp_path / "report.json"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=sample_results_with_issues,  # type: ignore[arg-type]
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    content = json.loads(output_path.read_text())
    issues = content["results"][0]["issues"]

    assert_that(issues).is_length(1)
    assert_that(issues[0]["file"]).is_equal_to("src/main.py")
    assert_that(issues[0]["line"]).is_equal_to(10)
    assert_that(issues[0]["code"]).is_equal_to("E001")
    assert_that(issues[0]["message"]).is_equal_to("Test error")


def test_write_json_file_includes_initial_issues(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., MockToolResult],
    mock_issue_factory: Callable[..., MockIssue],
) -> None:
    """Verify pre-fix issues from initial_issues are exported to JSON.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
        mock_issue_factory: Factory for creating mock issues.
    """
    output_path = tmp_path / "report.json"
    results = [
        mock_tool_result_factory(
            name="ruff",
            issues_count=0,
            initial_issues=[
                mock_issue_factory(file="a.py", line=1, code="F401", message="x"),
                mock_issue_factory(file="b.py", line=2, code="E501", message="y"),
            ],
        ),
    ]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.JSON,
        all_results=results,  # type: ignore[arg-type]
        action=Action.FIX,
        total_issues=0,
        total_fixed=2,
    )

    content = json.loads(output_path.read_text())
    initial = content["results"][0]["initial_issues"]

    assert_that(initial).is_length(2)
    assert_that(initial[0]["code"]).is_equal_to("F401")
    assert_that(initial[1]["code"]).is_equal_to("E501")
