"""Tests for the KtlintIssue dataclass."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.ktlint.ktlint_issue import KtlintIssue


def test_issue_defaults() -> None:
    """A KtlintIssue exposes sensible defaults."""
    issue = KtlintIssue()

    assert_that(issue.file).is_equal_to("")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.message).is_equal_to("")
    assert_that(issue.rule).is_equal_to("")


def test_issue_severity_defaults_to_error() -> None:
    """Findings default to error severity."""
    issue = KtlintIssue(rule="standard:filename")

    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_display_row_routes_rule_to_code() -> None:
    """The display ``code`` column is sourced from the ktlint rule id."""
    issue = KtlintIssue(
        file="src/Example.kt",
        line=2,
        column=15,
        message='Unexpected spacing before ":"',
        rule="standard:colon-spacing",
    )

    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("src/Example.kt")
    assert_that(row["line"]).is_equal_to("2")
    assert_that(row["column"]).is_equal_to("15")
    assert_that(row["code"]).is_equal_to("standard:colon-spacing")
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.ERROR))
    assert_that(row["message"]).contains("Unexpected spacing")
