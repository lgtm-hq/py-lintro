"""Tests for shellcheck parser field extraction."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.shellcheck.shellcheck_parser import parse_shellcheck_output

from .conftest import make_issue, make_shellcheck_output


@pytest.mark.parametrize("value", [None, False, [], {}])
def test_shellcheck_non_string_fields_use_defaults(value: object) -> None:
    """Null text and malformed codes cannot leak into shellcheck findings."""
    output = json.dumps([dict.fromkeys(["file", "level", "message", "code"], value)])
    issues = parse_shellcheck_output(output)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that((issue.file, issue.message, issue.code)).is_equal_to(("", "", ""))
    assert_that(issue.level).is_equal_to("error")


@pytest.mark.parametrize(
    ("level", "code"),
    [
        ("error", 1072),
        ("warning", 2086),
        ("info", 2034),
        ("style", 2129),
    ],
)
def test_parse_severity_levels(level: str, code: int) -> None:
    """Parse issues with different severity levels.

    Args:
        level: The expected severity level.
        code: The expected error code.
    """
    output = make_shellcheck_output([make_issue(level=level, code=code)])
    result = parse_shellcheck_output(output=output)

    assert_that(result).is_length(1)
    assert_that(result[0].level).is_equal_to(level)
    assert_that(result[0].code).is_equal_to(f"SC{code}")


def test_parse_extracts_all_fields() -> None:
    """Parse issue extracts all fields correctly."""
    output = make_shellcheck_output(
        [
            make_issue(
                file="script.sh",
                line=10,
                column=5,
                level="warning",
                code=2086,
                message="Double quote to prevent globbing and word splitting.",
                end_line=12,
                end_column=15,
            ),
        ],
    )
    result = parse_shellcheck_output(output=output)

    assert_that(result).is_length(1)
    issue = result[0]
    assert_that(issue.file).is_equal_to("script.sh")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.end_line).is_equal_to(12)
    assert_that(issue.column).is_equal_to(5)
    assert_that(issue.end_column).is_equal_to(15)
    assert_that(issue.level).is_equal_to("warning")
    assert_that(issue.code).is_equal_to("SC2086")
    assert_that(issue.message).is_equal_to(
        "Double quote to prevent globbing and word splitting.",
    )


def test_parse_handles_missing_optional_fields() -> None:
    """Parse issue with missing optional fields uses defaults."""
    output = make_shellcheck_output([make_issue()])
    result = parse_shellcheck_output(output=output)

    assert_that(result).is_length(1)
    assert_that(result[0].end_line).is_equal_to(0)
    assert_that(result[0].end_column).is_equal_to(0)


def test_parse_code_as_string() -> None:
    """Parse handles code as string (edge case)."""
    output = make_shellcheck_output([make_issue(code="SC2086")])
    result = parse_shellcheck_output(output=output)

    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("SC2086")
