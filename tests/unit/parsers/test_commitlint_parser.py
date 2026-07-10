"""Unit tests for the commitlint output parser.

These tests validate the parser against real commitlint reports captured from
``@commitlint/cli`` 21.2.0 (stored under ``commitlint_fixtures/``) plus
synthetic edge cases (ANSI colouring, malformed input).
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.commitlint.commitlint_issue import CommitlintIssue
from lintro.parsers.commitlint.commitlint_parser import parse_commitlint_output

_FIXTURES = Path(__file__).parent / "commitlint_fixtures"


def _load(name: str) -> str:
    """Read a captured commitlint fixture.

    Args:
        name: Fixture file name under ``commitlint_fixtures/``.

    Returns:
        The fixture file contents.
    """
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_empty_returns_empty_list() -> None:
    """Empty, whitespace, and None inputs yield no issues."""
    assert_that(parse_commitlint_output("")).is_equal_to([])
    assert_that(parse_commitlint_output("   \n\t ")).is_equal_to([])
    assert_that(parse_commitlint_output(None)).is_equal_to([])


def test_parse_error_report() -> None:
    """Parse a real error report into structured error issues."""
    issues = parse_commitlint_output(_load("errors.txt"))
    assert_that(len(issues)).is_equal_to(2)

    rules = [i.rule for i in issues]
    assert_that(rules).is_equal_to(["subject-empty", "type-empty"])

    first = issues[0]
    assert_that(first).is_instance_of(CommitlintIssue)
    assert_that(first.level).is_equal_to("error")
    assert_that(first.file).is_equal_to("bad commit message")
    assert_that(first.message).is_equal_to("subject may not be empty")
    assert_that(first.line).is_equal_to(0)
    assert_that(first.column).is_equal_to(0)
    assert_that(first.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_parse_warning_report() -> None:
    """Parse a real warning report and preserve warning severity."""
    issues = parse_commitlint_output(_load("warning.txt"))
    assert_that(len(issues)).is_equal_to(1)

    issue = issues[0]
    assert_that(issue.rule).is_equal_to("body-max-line-length")
    assert_that(issue.level).is_equal_to("warning")
    assert_that(issue.file).is_equal_to("feat: ok subject")
    assert_that(issue.message).contains("must not be longer than 20 characters")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_parse_multi_commit_range() -> None:
    """Parse a multi-commit range report, attributing issues per commit."""
    issues = parse_commitlint_output(_load("range.txt"))
    assert_that(len(issues)).is_equal_to(4)

    subjects = {i.file for i in issues}
    assert_that(subjects).is_equal_to({"another bad one", "Bad First Commit"})
    assert_that([i.rule for i in issues]).is_equal_to(
        ["subject-empty", "type-empty", "subject-empty", "type-empty"],
    )


def test_summary_line_is_not_parsed_as_issue() -> None:
    """The trailing summary line must not be captured as a violation."""
    issues = parse_commitlint_output(_load("errors.txt"))
    messages = [i.message for i in issues]
    assert_that(messages).does_not_contain("found 2 problems, 0 warnings")


def test_parse_strips_ansi_colour_codes() -> None:
    """ANSI-coloured output parses the same as plain output."""
    coloured = (
        "\x1b[90m⧗\x1b[39m   --- input ---\n"
        "bad commit message\n"
        "\x1b[31m✖\x1b[39m   subject may not be empty "
        "\x1b[90m[subject-empty]\x1b[39m\n"
    )
    issues = parse_commitlint_output(coloured)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].rule).is_equal_to("subject-empty")
    assert_that(issues[0].level).is_equal_to("error")


def test_malformed_lines_are_ignored() -> None:
    """Lines without a trailing rule bracket are skipped without error."""
    text = (
        "⧗   --- input ---\n"
        "some subject\n"
        "this is not a violation line\n"
        "✖   real violation here [type-enum]\n"
    )
    issues = parse_commitlint_output(text)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].rule).is_equal_to("type-enum")


def test_issue_display_row_maps_rule_and_severity() -> None:
    """The display row routes rule to code and level to severity."""
    issue = CommitlintIssue(
        file="feat: x",
        message="type may not be empty",
        rule="type-empty",
        level="error",
    )
    row = issue.to_display_row()
    assert_that(row["code"]).is_equal_to("type-empty")
    assert_that(row["message"]).is_equal_to("type may not be empty")
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.ERROR))


def test_issue_defaults_to_error_severity() -> None:
    """An issue with no explicit level falls back to ERROR severity."""
    issue = CommitlintIssue(message="something", rule="some-rule")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)
