"""Tests for format_fix_results function.

Verifies the two-table display for fix mode: "Detected issues" and
"Remaining issues" tables, plus the "All issues were auto-fixed" message.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.formatters.formatter import format_fix_results
from lintro.parsers.ruff.ruff_issue import RuffIssue


def _make_issue(
    file: str = "src/main.py",
    line: int = 1,
    code: str = "F401",
    message: str = "unused import",
) -> RuffIssue:
    """Create a RuffIssue for testing.

    Args:
        file: File path.
        line: Line number.
        code: Rule code.
        message: Issue message.

    Returns:
        A RuffIssue instance.
    """
    return RuffIssue(file=file, line=line, column=1, code=code, message=message)


def test_all_fixed_shows_auto_fixed_message() -> None:
    """When remaining is empty, show 'All issues were auto-fixed.'."""
    detected = [_make_issue()]
    result = format_fix_results(detected, remaining_issues=None)

    assert_that(result).contains("Detected issues (1)")
    assert_that(result).contains("All issues were auto-fixed.")


def test_all_fixed_with_empty_list() -> None:
    """When remaining is an empty list, show 'All issues were auto-fixed.'."""
    detected = [_make_issue(), _make_issue(line=2, code="E501")]
    result = format_fix_results(detected, remaining_issues=[])

    assert_that(result).contains("Detected issues (2)")
    assert_that(result).contains("All issues were auto-fixed.")


def test_partial_fix_shows_both_tables() -> None:
    """When some issues remain, show both Detected and Remaining tables."""
    detected = [
        _make_issue(code="F401"),
        _make_issue(line=5, code="E501", message="line too long"),
    ]
    remaining = [_make_issue(line=5, code="E501", message="line too long")]
    result = format_fix_results(detected, remaining_issues=remaining)

    assert_that(result).contains("Detected issues (2)")
    assert_that(result).contains("Remaining issues (1)")
    assert_that(result).does_not_contain("All issues were auto-fixed.")


def test_no_detected_issues() -> None:
    """When no issues were detected, return 'No issues found.'."""
    result = format_fix_results([], remaining_issues=None)

    assert_that(result).is_equal_to("No issues found.")


def test_json_format_merges_tables() -> None:
    """JSON format combines detected and remaining into one output."""
    detected = [_make_issue(code="F401")]
    remaining = [_make_issue(line=5, code="E501", message="line too long")]
    result = format_fix_results(
        detected,
        remaining_issues=remaining,
        output_format="json",
    )

    assert_that(result).does_not_contain("Detected issues")
    assert_that(result).does_not_contain("Remaining issues")
    # JSON output should contain both issues
    assert_that(result).contains("F401")
    assert_that(result).contains("E501")


def test_json_format_deduplicates() -> None:
    """JSON format deduplicates issues present in both detected and remaining."""
    issue = _make_issue(code="E501", message="line too long")
    result = format_fix_results(
        [issue],
        remaining_issues=[issue],
        output_format="json",
    )

    # Should only appear once after deduplication
    assert_that(result.count('"E501"')).is_equal_to(1)


def test_detected_table_contains_issue_details() -> None:
    """Detected table includes file, line, and code from issues."""
    detected = [_make_issue(file="app.py", line=42, code="W291")]
    result = format_fix_results(detected, remaining_issues=None)

    assert_that(result).contains("app.py")
    assert_that(result).contains("42")
    assert_that(result).contains("W291")
