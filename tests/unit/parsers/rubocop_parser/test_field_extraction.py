"""Tests for RuboCop parser field extraction."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.rubocop.rubocop_parser import parse_rubocop_output

from .conftest import make_offense, make_rubocop_output


def test_extracts_all_fields() -> None:
    """Parsing extracts every field from a single offense."""
    output = make_rubocop_output(
        {
            "app.rb": [
                make_offense(
                    cop_name="Layout/SpaceInsideParens",
                    severity="convention",
                    message="Space inside parentheses detected.",
                    correctable=True,
                    corrected=False,
                    start_line=2,
                    start_column=9,
                    last_line=2,
                    last_column=9,
                ),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("app.rb")
    assert_that(issue.line).is_equal_to(2)
    assert_that(issue.column).is_equal_to(9)
    assert_that(issue.end_line).is_equal_to(2)
    assert_that(issue.end_column).is_equal_to(9)
    assert_that(issue.code).is_equal_to("Layout/SpaceInsideParens")
    assert_that(issue.severity).is_equal_to("convention")
    assert_that(issue.department).is_equal_to("Layout")
    assert_that(issue.correctable).is_true()
    assert_that(issue.corrected).is_false()
    assert_that(issue.fixable).is_true()
    assert_that(issue.message).is_equal_to("Space inside parentheses detected.")


def test_correctable_drives_fixable() -> None:
    """The ``correctable`` flag maps directly onto ``fixable``."""
    output = make_rubocop_output(
        {
            "app.rb": [
                make_offense(
                    cop_name="Naming/MethodParameterName",
                    correctable=False,
                ),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].correctable).is_false()
    assert_that(issues[0].fixable).is_false()


@pytest.mark.parametrize(
    "severity",
    ["info", "refactor", "convention", "warning", "error", "fatal"],
)
def test_all_native_severities_preserved(severity: str) -> None:
    """Every RuboCop severity string is preserved verbatim on the issue."""
    output = make_rubocop_output(
        {"app.rb": [make_offense(severity=severity)]},
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to(severity)


def test_department_parsed_from_cop_name() -> None:
    """The department is the segment before the slash in the cop name."""
    output = make_rubocop_output(
        {
            "app.rb": [
                make_offense(cop_name="Lint/UselessAssignment"),
                make_offense(cop_name="Metrics/MethodLength"),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that([i.department for i in issues]).is_equal_to(["Lint", "Metrics"])


def test_multiple_files_and_offenses() -> None:
    """Offenses across multiple files are flattened, keeping their paths."""
    output = make_rubocop_output(
        {
            "a.rb": [make_offense(cop_name="Style/StringLiterals")],
            "b.rb": [
                make_offense(cop_name="Layout/IndentationWidth"),
                make_offense(cop_name="Lint/Void"),
            ],
        },
    )
    issues = parse_rubocop_output(output)

    assert_that(issues).is_length(3)
    assert_that({i.file for i in issues}).is_equal_to({"a.rb", "b.rb"})
