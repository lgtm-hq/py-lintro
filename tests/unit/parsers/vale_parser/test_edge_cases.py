"""Unit tests for Vale parser edge cases."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.vale.vale_parser import parse_vale_output

from .conftest import make_alert, make_output


def test_multiple_files() -> None:
    """Parser should flatten alerts across multiple files."""
    output = make_output(
        {
            "a.md": [make_alert(message="a1"), make_alert(message="a2")],
            "b.md": [make_alert(message="b1")],
        },
    )

    issues = parse_vale_output(output=output)

    assert_that(len(issues)).is_equal_to(3)
    files = {issue.file for issue in issues}
    assert_that(files).contains("a.md", "b.md")


def test_mixed_severities() -> None:
    """Parser should preserve each alert's native severity."""
    output = make_output(
        {
            "a.md": [
                make_alert(severity="error"),
                make_alert(severity="warning"),
                make_alert(severity="suggestion"),
            ],
        },
    )

    issues = parse_vale_output(output=output)

    severities = [issue.get_severity() for issue in issues]
    assert_that(severities).contains(
        SeverityLevel.ERROR,
        SeverityLevel.WARNING,
        SeverityLevel.INFO,
    )


def test_empty_alert_list_yields_no_issues() -> None:
    """A file with an empty alert list should contribute no issues."""
    output = make_output({"clean.md": []})

    assert_that(parse_vale_output(output=output)).is_empty()


def test_empty_object_yields_no_issues() -> None:
    """An empty JSON object (no files) should yield no issues."""
    assert_that(parse_vale_output(output="{}")).is_empty()


def test_missing_span_defaults_column_to_zero() -> None:
    """An alert without a Span should default the column to zero."""
    output = make_output({"a.md": [make_alert(span=[])]})

    issues = parse_vale_output(output=output)

    assert_that(issues[0].column).is_equal_to(0)


def test_missing_optional_fields_use_defaults() -> None:
    """Missing optional fields should coerce to empty/zero defaults."""
    output = make_output({"a.md": [{"Check": "Vale.Terms"}]})

    issues = parse_vale_output(output=output)

    issue = issues[0]
    assert_that(issue.check).is_equal_to("Vale.Terms")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.message).is_equal_to("")
    assert_that(issue.severity).is_equal_to("")


def test_negative_line_coerced_to_zero() -> None:
    """A non-positive line number should be coerced to zero."""
    output = make_output({"a.md": [make_alert(line=0)]})

    issues = parse_vale_output(output=output)

    assert_that(issues[0].line).is_equal_to(0)
