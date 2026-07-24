"""Tests for category-based issue section formatting."""

from __future__ import annotations

from assertpy import assert_that

from lintro.formatters.formatter import format_issues_by_category
from lintro.parsers.base_issue import BaseIssue
from lintro.parsers.oxlint.oxlint_issue import OxlintIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.output.file_writer import format_tool_output


def test_format_issues_by_category_sections() -> None:
    """Category grouping emits titled sections in taxonomy order."""
    issues = [
        RuffIssue(file="a.py", line=1, code="PERF401", message="slow"),
        OxlintIssue(
            file="b.tsx",
            line=2,
            code="jsx-a11y/alt-text",
            message="alt",
        ),
        BaseIssue(file="c.py", line=3, message="secret"),
    ]
    # Force security via existing category for the third issue.
    issues[2].category = "Security"

    output = format_issues_by_category(issues, output_format="plain", tool_name="ruff")

    assert_that(output).contains("Security (1)")
    assert_that(output).contains("Performance (1)")
    assert_that(output).contains("Accessibility (1)")
    assert_that(output.index("Security")).is_less_than(output.index("Performance"))
    assert_that(output.index("Performance")).is_less_than(
        output.index("Accessibility"),
    )


def test_format_tool_output_respects_group_by_category() -> None:
    """format_tool_output sections by category when group_by=category."""
    issues = [
        RuffIssue(file="a.py", line=1, code="PERF401", message="slow"),
        RuffIssue(file="a.py", line=2, code="F401", message="unused"),
    ]
    output = format_tool_output(
        tool_name="ruff",
        output="",
        output_format="plain",
        issues=issues,
        group_by="category",
    )
    assert_that(output).contains("Performance (1)")
    assert_that(issues[0].category).is_equal_to("Performance")
