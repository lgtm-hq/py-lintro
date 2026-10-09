"""Tests for the RubocopIssue dataclass model."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.rubocop.rubocop_issue import RubocopIssue


def test_defaults() -> None:
    """A default RubocopIssue has empty/false field values."""
    issue = RubocopIssue()
    assert_that(issue.file).is_equal_to("")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.code).is_equal_to("")
    assert_that(issue.severity).is_equal_to("")
    assert_that(issue.department).is_equal_to("")
    assert_that(issue.correctable).is_false()
    assert_that(issue.corrected).is_false()
    assert_that(issue.fixable).is_false()


def test_default_severity_is_warning() -> None:
    """An offense with no native severity falls back to WARNING."""
    issue = RubocopIssue(code="Layout/SpaceInsideParens")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_convention_maps_to_warning() -> None:
    """RuboCop 'convention' severity normalizes to WARNING."""
    issue = RubocopIssue(severity="convention")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_refactor_maps_to_info() -> None:
    """RuboCop 'refactor' severity normalizes to INFO."""
    issue = RubocopIssue(severity="refactor")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.INFO)


def test_fatal_maps_to_error() -> None:
    """RuboCop 'fatal' severity normalizes to ERROR."""
    issue = RubocopIssue(severity="fatal")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_display_row_reports_fixable() -> None:
    """A correctable offense renders as fixable in the display row."""
    issue = RubocopIssue(
        file="app.rb",
        line=3,
        column=10,
        code="Style/StringLiterals",
        message="Prefer single-quoted strings.",
        severity="convention",
        fixable=True,
    )
    row = issue.to_display_row()
    assert_that(row["code"]).is_equal_to("Style/StringLiterals")
    assert_that(row["fixable"]).is_equal_to("Yes")
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.WARNING))


def test_display_row_non_fixable() -> None:
    """A non-correctable offense renders with an empty fixable flag."""
    issue = RubocopIssue(
        code="Naming/MethodParameterName",
        message="Method parameter must be at least 3 characters long.",
        severity="convention",
        fixable=False,
    )
    row = issue.to_display_row()
    assert_that(row["fixable"]).is_equal_to("")
