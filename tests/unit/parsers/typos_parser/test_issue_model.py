"""Tests for the TyposIssue model and its display contract."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.typos.typos_issue import TyposIssue


def test_typos_issue_defaults() -> None:
    """A default TyposIssue exposes sensible field defaults."""
    issue = TyposIssue()

    assert_that(issue.file).is_equal_to("")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.level).is_equal_to("error")
    assert_that(issue.corrections).is_equal_to([])
    assert_that(issue.fixable).is_true()


def test_typos_issue_display_row() -> None:
    """to_display_row exposes the unified keys with typos values."""
    issue = TyposIssue(
        file="README.md",
        line=3,
        column=19,
        message='"teh" should be "the"',
        typo="teh",
        corrections=["the"],
        byte_offset=18,
    )

    row = issue.to_display_row()

    assert_that(row["file"]).is_equal_to("README.md")
    assert_that(row["line"]).is_equal_to("3")
    assert_that(row["column"]).is_equal_to("19")
    assert_that(row["message"]).is_equal_to('"teh" should be "the"')
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.ERROR))
    assert_that(row["fixable"]).is_equal_to("Yes")


def test_typos_issue_severity_is_error() -> None:
    """Typos findings normalize to ERROR severity."""
    issue = TyposIssue(typo="teh", corrections=["the"])

    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_typos_issue_zero_location_renders_dash() -> None:
    """Unknown line/column render as a dash in the display row."""
    issue = TyposIssue(file="x.txt", line=0, column=0)

    row = issue.to_display_row()

    assert_that(row["line"]).is_equal_to("-")
    assert_that(row["column"]).is_equal_to("-")
