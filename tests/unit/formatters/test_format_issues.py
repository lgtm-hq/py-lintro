"""Tests for format_issues and format_issues_with_sections functions.

Tests the unified table formatting of issues from different tools,
verifying correct column structure and content.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.display_column import STANDARD_COLUMNS, DisplayColumn
from lintro.formatters.formatter import (
    format_issues,
    format_issues_with_sections,
)
from lintro.parsers.bandit.bandit_issue import BanditIssue
from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.black.black_issue import BlackIssue
from lintro.parsers.ruff.ruff_format_issue import RuffFormatIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue

# =============================================================================
# Tests for STANDARD_COLUMNS constant
# =============================================================================


def test_standard_columns_has_expected_fields() -> None:
    """Verify STANDARD_COLUMNS contains expected fields."""
    assert_that(STANDARD_COLUMNS).is_equal_to(
        [
            DisplayColumn.FILE,
            DisplayColumn.LINE,
            DisplayColumn.COLUMN,
            DisplayColumn.CODE,
            DisplayColumn.SEVERITY,
            DisplayColumn.FIXABLE,
            DisplayColumn.MESSAGE,
        ],
    )


# =============================================================================
# Tests for format_issues with RuffIssue
# =============================================================================


def test_format_issues_with_ruff_issue_contains_standard_columns() -> None:
    """Verify RuffIssue formatted output contains all standard column headers."""
    issues = [
        RuffIssue(
            file="src/main.py",
            line=10,
            column=5,
            code="F401",
            message="unused import",
            fixable=True,
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("File")
    assert_that(result).contains("Line")
    assert_that(result).contains("Column")
    assert_that(result).contains("Code")
    assert_that(result).contains("Severity")
    assert_that(result).contains("Fixable")
    assert_that(result).contains("Message")


def test_format_issues_with_ruff_issue_contains_issue_data() -> None:
    """Verify RuffIssue formatted output contains the actual issue data."""
    issues = [
        RuffIssue(
            file="src/main.py",
            line=10,
            column=5,
            code="F401",
            message="unused import",
            fixable=True,
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("src/main.py")
    assert_that(result).contains("10")
    assert_that(result).contains("5")
    assert_that(result).contains("F401")
    assert_that(result).contains("unused import")


def test_format_issues_shows_fixable_status() -> None:
    """Verify Fixable column shows Yes for fixable=True status."""
    issues = [
        RuffIssue(
            file="src/main.py",
            line=10,
            column=5,
            code="F401",
            message="unused import",
            fixable=True,
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("Fixable")
    assert_that(result).contains("Yes")


def test_format_issues_shows_non_fixable_status() -> None:
    """Verify Fixable column is empty for fixable=False status."""
    issues = [
        RuffIssue(
            file="src/main.py",
            line=10,
            column=5,
            code="D100",
            message="missing docstring",
            fixable=False,
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("Fixable")
    # Non-fixable issues show empty string, not "Yes"
    assert_that(result).does_not_contain("Yes")


def test_format_issues_shows_severity() -> None:
    """Verify Severity column shows severity values."""
    issues = [
        BanditIssue(
            file="src/main.py",
            line=10,
            col_offset=5,
            test_id="B101",
            issue_text="Use assert_that instead of assert",
            issue_severity="HIGH",
            issue_confidence="HIGH",
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("Severity")
    assert_that(result).contains("ERROR")


# =============================================================================
# Tests for format_issues with BlackIssue
# =============================================================================


def test_format_issues_with_black_issue_contains_standard_columns() -> None:
    """Verify BlackIssue formatted output contains all standard column headers.

    BlackIssue doesn't have meaningful Line/Column/Code values, but the
    table should still show all standard columns for consistency.
    """
    issues = [
        BlackIssue(
            file="src/main.py",
            message="Would reformat file",
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("File")
    assert_that(result).contains("Line")
    assert_that(result).contains("Column")
    assert_that(result).contains("Code")
    assert_that(result).contains("Severity")
    assert_that(result).contains("Fixable")
    assert_that(result).contains("Message")
    assert_that(result).contains("src/main.py")
    assert_that(result).contains("Would reformat file")


# =============================================================================
# Tests for format_issues with RuffFormatIssue
# =============================================================================


def test_format_issues_with_ruff_format_issue_contains_standard_columns() -> None:
    """Verify RuffFormatIssue formatted output contains all standard columns.

    RuffFormatIssue has a fixed code of 'FORMAT' and message 'Would reformat file'.
    """
    issues = [
        RuffFormatIssue(
            file="src/main.py",
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("File")
    assert_that(result).contains("Line")
    assert_that(result).contains("Column")
    assert_that(result).contains("Code")
    assert_that(result).contains("Severity")
    assert_that(result).contains("Fixable")
    assert_that(result).contains("Message")
    assert_that(result).contains("src/main.py")
    assert_that(result).contains("FORMAT")
    assert_that(result).contains("Would reformat file")


# =============================================================================
# Tests for format_issues with BanditIssue
# =============================================================================


def test_format_issues_with_bandit_issue_uses_display_field_map() -> None:
    """Verify BanditIssue uses its custom DISPLAY_FIELD_MAP for columns.

    BanditIssue maps test_id -> code, issue_text -> message, issue_severity -> severity.
    """
    issues = [
        BanditIssue(
            file="src/main.py",
            line=10,
            col_offset=5,
            test_id="B101",
            issue_text="assert used",
            issue_severity="LOW",
            issue_confidence="HIGH",
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("src/main.py")
    assert_that(result).contains("B101")
    assert_that(result).contains("assert used")


def test_format_issues_with_bandit_issue_shows_severity() -> None:
    """Verify BanditIssue severity is shown in output by default."""
    issues = [
        BanditIssue(
            file="src/main.py",
            line=10,
            col_offset=5,
            test_id="B101",
            issue_text="assert used",
            issue_severity="HIGH",
            issue_confidence="HIGH",
        ),
    ]

    result = format_issues(issues, output_format="grid")

    assert_that(result).contains("Severity")
    assert_that(result).contains("ERROR")


# =============================================================================
# Tests for format_issues_with_sections
# =============================================================================


def test_format_issues_with_sections_groups_by_fixable() -> None:
    """Verify format_issues_with_sections groups issues by fixable status."""
    issues = [
        RuffIssue(
            file="a.py",
            line=1,
            column=1,
            code="F401",
            message="unused import",
            fixable=True,
        ),
        RuffIssue(
            file="b.py",
            line=1,
            column=1,
            code="D100",
            message="missing docstring",
            fixable=False,
        ),
    ]

    result = format_issues_with_sections(issues, group_by_fixable=True)

    assert_that(result).contains("Auto-fixable issues")
    assert_that(result).contains("Not auto-fixable issues")
    assert_that(result).contains("a.py")
    assert_that(result).contains("b.py")


def test_format_issues_with_sections_only_fixable() -> None:
    """Verify format_issues_with_sections handles only fixable issues."""
    issues = [
        RuffIssue(
            file="a.py",
            line=1,
            column=1,
            code="F401",
            message="unused import",
            fixable=True,
        ),
    ]

    result = format_issues_with_sections(issues, group_by_fixable=True)

    assert_that(result).contains("Auto-fixable issues")
    assert_that(result).does_not_contain("Not auto-fixable issues")


def test_format_issues_with_sections_only_non_fixable() -> None:
    """Verify format_issues_with_sections handles only non-fixable issues."""
    issues = [
        RuffIssue(
            file="a.py",
            line=1,
            column=1,
            code="D100",
            message="missing docstring",
            fixable=False,
        ),
    ]

    result = format_issues_with_sections(issues, group_by_fixable=True)

    assert_that(result).does_not_contain("Auto-fixable issues")
    assert_that(result).contains("Not auto-fixable issues")


def test_format_issues_with_sections_without_grouping() -> None:
    """Verify format_issues_with_sections without grouping returns single table."""
    issues = [
        RuffIssue(
            file="a.py",
            line=1,
            column=1,
            code="F401",
            message="unused import",
            fixable=True,
        ),
        RuffIssue(
            file="b.py",
            line=1,
            column=1,
            code="D100",
            message="missing docstring",
            fixable=False,
        ),
    ]

    result = format_issues_with_sections(issues, group_by_fixable=False)

    # Should not have section headers when not grouping
    assert_that(result).does_not_contain("Auto-fixable issues")
    assert_that(result).does_not_contain("Not auto-fixable issues")
    # But should still contain the data
    assert_that(result).contains("a.py")
    assert_that(result).contains("b.py")


# =============================================================================
# Tests for consistent column output across tools
# =============================================================================


@pytest.mark.parametrize(
    "issue",
    [
        RuffIssue(
            file="test.py",
            line=1,
            column=1,
            code="F401",
            message="test",
            fixable=True,
        ),
        BlackIssue(file="test.py", message="test"),
        RuffFormatIssue(file="test.py"),
        BanditIssue(
            file="test.py",
            line=1,
            col_offset=1,
            test_id="B101",
            issue_text="test",
            issue_severity="LOW",
            issue_confidence="HIGH",
        ),
    ],
    ids=["ruff", "black", "ruff_format", "bandit"],
)
def test_all_tool_issues_produce_tables_with_standard_columns(issue: BaseIssue) -> None:
    """Verify all tool issue types produce tables with standard columns.

    This ensures consistent output format regardless of which tool
    generated the issues.

    Args:
        issue: A BaseIssue subclass instance from any supported tool.
    """
    result = format_issues([issue], output_format="grid")

    # All tools should produce tables with these column headers
    assert_that(result).contains("File")
    assert_that(result).contains("Line")
    assert_that(result).contains("Column")
    assert_that(result).contains("Code")
    assert_that(result).contains("Severity")
    assert_that(result).contains("Fixable")
    assert_that(result).contains("Message")


# =============================================================================
# Tests for empty issues
# =============================================================================


def test_format_issues_with_empty_list_returns_no_issues_message() -> None:
    """Verify format_issues with empty list returns 'No issues found' message."""
    result = format_issues([], output_format="grid")

    assert_that(result).is_equal_to("No issues found.")


def test_format_issues_with_sections_empty_list_returns_no_issues_message() -> None:
    """Verify format_issues_with_sections with empty list.

    Returns 'No issues found' message.
    """
    result = format_issues_with_sections([], group_by_fixable=True)

    assert_that(result).is_equal_to("No issues found.")
