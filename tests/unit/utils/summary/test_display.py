"""Unit tests for print_summary_table in summary_tables module.

Tests cover:
- print_summary_table for CHECK, FIX, and TEST actions
- Multiple tools display
- Edge cases including empty results and unknown tools
- Module constants and their usage
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.summary_tables import (
    DEFAULT_REMAINING_COUNT,
    print_summary_table,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.unit.utils.conftest import FakeToolResult


# =============================================================================
# Tests for print_summary_table with CHECK action
# =============================================================================


def test_check_success_no_issues(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display passing check with no issues shows PASS status and tool name.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(name="ruff", success=True, issues_count=0)

    print_summary_table(capture, Action.CHECK, [result])

    combined = "".join(output)
    assert_that(combined).contains("ruff")
    assert_that(combined).contains("PASS")


def test_check_with_issues(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display failing check with issues shows FAIL status and issue count.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(name="ruff", success=True, issues_count=5)

    print_summary_table(capture, Action.CHECK, [result])

    combined = "".join(output)
    assert_that(combined).contains("ruff")
    assert_that(combined).contains("FAIL")
    assert_that(combined).contains("5")


def test_check_execution_failure(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display check with execution failure shows FAIL status.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="ruff",
        success=False,
        issues_count=0,
        output="timeout occurred",
    )

    print_summary_table(capture, Action.CHECK, [result])

    combined = "".join(output)
    assert_that(combined).contains("FAIL")


def test_check_skipped_tool(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display skipped status for version check failures.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="ruff",
        success=False,
        issues_count=0,
        output="Skipping ruff: version check failed",
    )

    print_summary_table(capture, Action.CHECK, [result])

    combined = "".join(output)
    assert_that(combined).contains("SKIPPED")


# =============================================================================
# Tests for print_summary_table with FIX action
# =============================================================================


def test_fix_with_fixed_count(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display fixed count and remaining for fix action with columns present.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="black",
        success=True,
        issues_count=0,
        fixed_issues_count=3,
        remaining_issues_count=0,
    )

    print_summary_table(capture, Action.FIX, [result])

    combined = "".join(output)
    assert_that(combined).contains("black")
    assert_that(combined).contains("PASS")
    assert_that(combined).contains("Fixed")
    assert_that(combined).contains("AI-Applied")
    assert_that(combined).contains("AI-Resolved")
    assert_that(combined).contains("Remaining")


def test_fix_with_remaining_issues(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display remaining issues for fix action when some issues cannot be fixed.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="black",
        success=True,
        issues_count=0,
        fixed_issues_count=5,
        remaining_issues_count=2,
    )

    print_summary_table(capture, Action.FIX, [result])

    combined = "".join(output)
    assert_that(combined).contains("black")


def test_fix_no_files_shows_zero(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display PASS with 0 fixed/remaining when no files to format.

    Note: "No files to format" means the tool ran successfully but found no
    files - this is PASS with 0 issues, not SKIPPED (consistent with check mode).

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="black",
        success=True,
        issues_count=0,
        output="No files to format",
    )

    print_summary_table(capture, Action.FIX, [result])

    combined = "".join(output)
    assert_that(combined).contains("PASS")
    # Should show 0 for both Fixed and Remaining, not SKIPPED
    assert_that(combined).does_not_contain("SKIPPED")


def test_fix_parsing_remaining_from_output(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Parse remaining issues from output when not explicitly provided.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="black",
        success=True,
        issues_count=3,
        output="Found 2 issue(s) that cannot be auto-fixed",
    )

    print_summary_table(capture, Action.FIX, [result])

    combined = "".join(output)
    assert_that(combined).contains("black")


def test_fix_shows_ai_fixed_count_from_metadata(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display AI columns when AI metadata provides per-tool counts.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="ruff",
        success=False,
        fixed_issues_count=6,
        remaining_issues_count=5,
    )
    result.ai_metadata = {
        "applied_count": 4,
        "verified_count": 3,
        "unverified_count": 1,
    }

    print_summary_table(capture, Action.FIX, [result])

    combined = "".join(output)
    assert_that(combined).contains("AI-Applied")
    assert_that(combined).contains("AI-Resolved")
    assert_that(combined).contains("4")
    assert_that(combined).contains("3")
    assert_that(combined).contains("1 unresolved")


# =============================================================================
# Tests for print_summary_table with TEST action
# =============================================================================


def test_pytest_with_summary(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display pytest summary with detailed metrics including all columns.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="pytest",
        success=True,
        issues_count=0,
        pytest_summary={
            "passed": 10,
            "failed": 2,
            "skipped": 1,
            "duration": 1.5,
            "total": 13,
        },
    )

    print_summary_table(capture, Action.TEST, [result])

    combined = "".join(output)
    assert_that(combined).contains("pytest")
    assert_that(combined).contains("Passed")
    assert_that(combined).contains("Failed")
    assert_that(combined).contains("Skipped")
    assert_that(combined).contains("Duration")


def test_non_pytest_test_tool(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display basic pass/fail for non-pytest tools in test action.

    Tool names with underscores are displayed with hyphens for consistency.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="other_test_runner",
        success=True,
        issues_count=0,
    )

    print_summary_table(capture, Action.TEST, [result])

    combined = "".join(output)
    # Underscores are converted to hyphens for display
    assert_that(combined).contains("other-test-runner")
    assert_that(combined).does_not_contain(
        "other_test_runner",
    )  # original with underscore
    assert_that(combined).contains("PASS")


# =============================================================================
# Tests for print_summary_table with multiple tools
# =============================================================================


def test_multiple_tools_displayed(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display all tools in the summary table when multiple tools are run.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    results = [
        fake_tool_result_factory(name="ruff", success=True, issues_count=0),
        fake_tool_result_factory(name="black", success=True, issues_count=2),
        fake_tool_result_factory(name="mypy", success=False, issues_count=5),
    ]

    print_summary_table(capture, Action.CHECK, results)

    combined = "".join(output)
    assert_that(combined).contains("ruff")
    assert_that(combined).contains("black")
    assert_that(combined).contains("mypy")


def test_tools_sorted_alphabetically(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Display tools in alphabetical order regardless of input order.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    # Input in non-alphabetical order: ruff, bandit, clippy
    results = [
        fake_tool_result_factory(name="ruff", success=True, issues_count=0),
        fake_tool_result_factory(name="bandit", success=True, issues_count=0),
        fake_tool_result_factory(name="clippy", success=True, issues_count=0),
    ]

    print_summary_table(capture, Action.CHECK, results)

    combined = "".join(output)
    # Verify alphabetical order: bandit < clippy < ruff
    bandit_pos = combined.find("bandit")
    clippy_pos = combined.find("clippy")
    ruff_pos = combined.find("ruff")

    assert_that(bandit_pos).is_less_than(clippy_pos)
    assert_that(clippy_pos).is_less_than(ruff_pos)


# =============================================================================
# Tests for edge cases in print_summary_table
# =============================================================================


def test_empty_results_list(
    console_capture: tuple[Callable[[str], None], list[str]],
) -> None:
    """Handle empty results list gracefully by still producing output.

    Args:
        console_capture: Mock console output capture.
    """
    capture, output = console_capture

    print_summary_table(capture, Action.CHECK, [])

    # Should output something (table headers even if empty)
    assert_that(output).is_not_empty()


def test_unknown_tool_name(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Handle unknown tool name gracefully with underscore-to-hyphen conversion.

    Tool names with underscores are displayed with hyphens for consistency
    with actual CLI tool naming conventions.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="unknown_tool_xyz",
        success=True,
        issues_count=0,
    )

    print_summary_table(capture, Action.CHECK, [result])

    combined = "".join(output)
    # Underscores are converted to hyphens for display
    assert_that(combined).contains("unknown-tool-xyz")
    assert_that(combined).does_not_contain(
        "unknown_tool_xyz",
    )  # original with underscore


# =============================================================================
# Tests for module constants
# =============================================================================


def test_default_remaining_count_is_question_mark() -> None:
    """Verify DEFAULT_REMAINING_COUNT is the expected sentinel value."""
    assert_that(DEFAULT_REMAINING_COUNT).is_equal_to("?")
    assert_that(DEFAULT_REMAINING_COUNT).is_instance_of(str)


def test_default_remaining_count_used_in_fix_output(
    console_capture: tuple[Callable[[str], None], list[str]],
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify DEFAULT_REMAINING_COUNT is used when remaining count is unknown.

    When a tool fails but the remaining issue count cannot be determined,
    the constant should appear in the output as a fallback indicator.

    Args:
        console_capture: Mock console output capture.
        fake_tool_result_factory: Factory for creating fake tool results.
    """
    capture, output = console_capture
    result = fake_tool_result_factory(
        name="ruff",
        success=False,
        issues_count=0,
        output="some remaining issues exist",
        remaining_issues_count=None,
        fixed_issues_count=None,
    )

    print_summary_table(capture, Action.FIX, [result])

    combined = "".join(output)
    # The "?" should appear in the remaining column when count is unknown
    assert_that(combined).contains(DEFAULT_REMAINING_COUNT)
