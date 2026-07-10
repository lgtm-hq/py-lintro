"""Unit tests for the ValeIssue model."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.vale.vale_issue import ValeIssue


def test_vale_issue_display_row() -> None:
    """ValeIssue should produce a correct display row."""
    issue = ValeIssue(
        file="docs/guide.md",
        line=3,
        column=7,
        message="'the' is repeated!",
        check="Vale.Repetition",
        style="Vale",
        severity="error",
        match="The the",
    )

    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("docs/guide.md")
    assert_that(row["line"]).is_equal_to("3")
    assert_that(row["column"]).is_equal_to("7")
    assert_that(row["code"]).is_equal_to("Vale.Repetition")
    assert_that(row["message"]).is_equal_to("'the' is repeated!")
    assert_that(row["severity"]).is_equal_to("ERROR")


def test_vale_issue_severity_normalization() -> None:
    """Native Vale severities should normalize to SeverityLevel values."""
    error = ValeIssue(severity="error")
    warning = ValeIssue(severity="warning")
    suggestion = ValeIssue(severity="suggestion")

    assert_that(error.get_severity()).is_equal_to(SeverityLevel.ERROR)
    assert_that(warning.get_severity()).is_equal_to(SeverityLevel.WARNING)
    assert_that(suggestion.get_severity()).is_equal_to(SeverityLevel.INFO)


def test_vale_issue_default_severity_when_missing() -> None:
    """A missing severity should fall back to the WARNING default."""
    issue = ValeIssue(severity="")

    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_vale_issue_display_row_defaults() -> None:
    """Default field values should render sensible display placeholders."""
    issue = ValeIssue()

    row = issue.to_display_row()

    assert_that(row["line"]).is_equal_to("-")
    assert_that(row["column"]).is_equal_to("-")
    assert_that(row["code"]).is_equal_to("")
    assert_that(row["fixable"]).is_equal_to("")
