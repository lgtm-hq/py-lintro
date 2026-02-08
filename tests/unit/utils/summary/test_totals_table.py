"""Unit tests for totals table generation.

Tests for count_affected_files helper and print_totals_table function
in lintro.utils.summary_tables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.summary_tables import count_affected_files, print_totals_table

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.unit.utils.conftest import FakeIssue, FakeToolResult


# =============================================================================
# count_affected_files Tests
# =============================================================================


def test_count_affected_files_empty_results() -> None:
    """Verify count_affected_files returns 0 for empty results list."""
    assert_that(count_affected_files([])).is_equal_to(0)


def test_count_affected_files_no_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify count_affected_files returns 0 when results have no issues.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    results = [fake_tool_result_factory(issues=[])]
    assert_that(count_affected_files(results)).is_equal_to(0)


def test_count_affected_files_single_file(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fake_issue_factory: Callable[..., FakeIssue],
) -> None:
    """Verify count_affected_files counts a single affected file.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fake_issue_factory: Factory for creating FakeIssue instances.
    """
    results = [
        fake_tool_result_factory(
            issues=[fake_issue_factory(file="src/main.py")],
        ),
    ]
    assert_that(count_affected_files(results)).is_equal_to(1)


def test_count_affected_files_deduplication_within_tool(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fake_issue_factory: Callable[..., FakeIssue],
) -> None:
    """Verify count_affected_files deduplicates files within a single tool.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fake_issue_factory: Factory for creating FakeIssue instances.
    """
    results = [
        fake_tool_result_factory(
            issues=[
                fake_issue_factory(file="src/main.py"),
                fake_issue_factory(file="src/main.py"),
                fake_issue_factory(file="src/utils.py"),
            ],
        ),
    ]
    assert_that(count_affected_files(results)).is_equal_to(2)


def test_count_affected_files_deduplication_across_tools(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fake_issue_factory: Callable[..., FakeIssue],
) -> None:
    """Verify count_affected_files deduplicates files across multiple tools.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fake_issue_factory: Factory for creating FakeIssue instances.
    """
    results = [
        fake_tool_result_factory(
            issues=[fake_issue_factory(file="src/main.py")],
        ),
        fake_tool_result_factory(
            issues=[
                fake_issue_factory(file="src/main.py"),
                fake_issue_factory(file="src/other.py"),
            ],
        ),
    ]
    assert_that(count_affected_files(results)).is_equal_to(2)


def test_count_affected_files_empty_file_paths_excluded(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fake_issue_factory: Callable[..., FakeIssue],
) -> None:
    """Verify count_affected_files excludes issues with empty file paths.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fake_issue_factory: Factory for creating FakeIssue instances.
    """
    results = [
        fake_tool_result_factory(
            issues=[
                fake_issue_factory(file=""),
                fake_issue_factory(file="src/valid.py"),
            ],
        ),
    ]
    assert_that(count_affected_files(results)).is_equal_to(1)


def test_count_affected_files_path_objects_deduplicated(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fake_issue_factory: Callable[..., FakeIssue],
) -> None:
    """Verify count_affected_files deduplicates Path objects and strings.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fake_issue_factory: Factory for creating FakeIssue instances.
    """
    from pathlib import Path

    results = [
        fake_tool_result_factory(
            issues=[
                fake_issue_factory(file=Path("src/main.py")),
                fake_issue_factory(file="src/main.py"),
            ],
        ),
    ]
    assert_that(count_affected_files(results)).is_equal_to(1)


def test_count_affected_files_no_issues_attribute() -> None:
    """Verify count_affected_files handles objects without issues attribute."""

    class NoIssuesResult:
        name: str = "tool"

    assert_that(count_affected_files([NoIssuesResult()])).is_equal_to(0)


# =============================================================================
# print_totals_table Tests - CHECK Mode
# =============================================================================


def test_totals_table_check_mode_contains_total_issues(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify CHECK mode table contains Total Issues row.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.CHECK,
        total_issues=5,
        affected_files=2,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Total Issues")
    assert_that(combined).contains("5")


def test_totals_table_check_mode_contains_affected_files(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify CHECK mode table contains Affected Files row.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.CHECK,
        total_issues=5,
        affected_files=3,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Affected Files")
    assert_that(combined).contains("3")


def test_totals_table_check_mode_does_not_contain_fix_rows(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify CHECK mode table does not contain FIX-specific rows.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.CHECK,
        total_issues=5,
        affected_files=2,
    )
    combined = "\n".join(output)
    assert_that(combined).does_not_contain("Fixed Issues")
    assert_that(combined).does_not_contain("Remaining Issues")


# =============================================================================
# print_totals_table Tests - TEST Mode
# =============================================================================


def test_totals_table_test_mode_uses_check_layout(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify TEST mode table uses same layout as CHECK mode.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.TEST,
        total_issues=4,
        affected_files=1,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Total Issues")
    assert_that(combined).contains("Affected Files")


# =============================================================================
# print_totals_table Tests - FIX Mode
# =============================================================================


def test_totals_table_fix_mode_contains_fixed_issues(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify FIX mode table contains Fixed Issues row.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.FIX,
        total_fixed=10,
        total_remaining=2,
        affected_files=5,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Fixed Issues")
    assert_that(combined).contains("10")


def test_totals_table_fix_mode_contains_remaining_issues(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify FIX mode table contains Remaining Issues row.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.FIX,
        total_fixed=10,
        total_remaining=2,
        affected_files=5,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Remaining Issues")
    assert_that(combined).contains("2")


def test_totals_table_fix_mode_contains_affected_files(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify FIX mode table contains Affected Files row.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.FIX,
        total_fixed=10,
        total_remaining=2,
        affected_files=5,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Affected Files")
    assert_that(combined).contains("5")


def test_totals_table_fix_mode_does_not_contain_total_issues(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify FIX mode table does not contain Total Issues row.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.FIX,
        total_fixed=10,
        total_remaining=2,
        affected_files=5,
    )
    combined = "\n".join(output)
    assert_that(combined).does_not_contain("Total Issues")


# =============================================================================
# print_totals_table Tests - Format and Header
# =============================================================================


def test_totals_table_uses_grid_format(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify totals table uses grid format with expected characters.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.CHECK,
        total_issues=1,
        affected_files=1,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("+")
    assert_that(combined).contains("|")


def test_totals_table_contains_header(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify totals table output contains TOTALS header.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.CHECK,
        total_issues=0,
        affected_files=0,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("TOTALS")


def test_totals_table_contains_metric_count_headers(
    console_capture: tuple[Callable[..., None], list[str]],
) -> None:
    """Verify totals table contains Metric and Count column headers.

    Args:
        console_capture: Fixture for capturing console output.
    """
    capture_func, output = console_capture
    print_totals_table(
        console_output_func=capture_func,
        action=Action.CHECK,
        total_issues=0,
        affected_files=0,
    )
    combined = "\n".join(output)
    assert_that(combined).contains("Metric")
    assert_that(combined).contains("Count")
