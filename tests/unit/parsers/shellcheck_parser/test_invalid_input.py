"""Tests for shellcheck parser handling of invalid/empty input."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.shellcheck.shellcheck_parser import (
    parse_shellcheck_output,
    parse_shellcheck_output_with_stats,
)


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
        "   \n  \n   ",
    ],
    ids=["none", "empty", "whitespace_only"],
)
def test_parse_returns_empty_for_no_content(
    output: str | None,
) -> None:
    """Parse empty, None, or whitespace-only output returns empty list.

    Args:
        output: The shellcheck output to parse.
    """
    result = parse_shellcheck_output(output=output)
    assert_that(result).is_empty()


def test_parse_returns_empty_for_invalid_json() -> None:
    """Parse invalid JSON returns empty list."""
    result = parse_shellcheck_output(output="not valid json")
    assert_that(result).is_empty()


def test_parse_returns_empty_for_non_array_json() -> None:
    """Parse JSON that is not an array returns empty list."""
    result = parse_shellcheck_output(output='{"key": "value"}')
    assert_that(result).is_empty()


@pytest.mark.parametrize(
    "output",
    [
        '{"comments": null}',
        '{"comments": false}',
        '{"comments": 0}',
        '{"comments": ""}',
        '{"comments": "not a list"}',
        '{"comments": {}}',
        '{"comments": {"file": "script.sh", "message": "warning"}}',
        "{}",
        '{"other": []}',
        "null",
        "false",
        "0",
        '""',
        "not valid json",
    ],
)
def test_parse_malformed_comments_returns_no_findings(output: str) -> None:
    """Invalid containers count as failures without changing the list-only API."""
    issues, failures = parse_shellcheck_output_with_stats(output)
    assert_that((issues, failures)).is_equal_to(([], 1))
    assert_that(parse_shellcheck_output(output)).is_equal_to(issues)


@pytest.mark.parametrize("output", [None, "", " \n ", "[]", '{"comments": []}'])
def test_parse_empty_reports_have_no_failures(output: str | None) -> None:
    """Absent output and supported empty reports remain clean controls."""
    assert_that(parse_shellcheck_output_with_stats(output)).is_equal_to(([], 0))


@pytest.mark.parametrize(
    "output",
    [
        '[{"file": "script.sh", "code": 2086, "message": "Quote this"}]',
        '{"comments": [{"file": "script.sh", "code": 2086, "message": "Quote this"}]}',
    ],
    ids=["legacy", "json1"],
)
def test_parse_supported_report_formats(output: str) -> None:
    """Both report formats retain findings through the stats and list APIs."""
    issues, failures = parse_shellcheck_output_with_stats(output)
    assert_that(failures).is_zero()
    assert_that(issues).is_length(1)
    assert_that((issues[0].file, issues[0].code, issues[0].message)).is_equal_to(
        ("script.sh", "SC2086", "Quote this"),
    )
    assert_that(parse_shellcheck_output(output)).is_equal_to(issues)
