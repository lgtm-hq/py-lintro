"""Unit tests for cargo-deny parser."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.cargo_deny.cargo_deny_parser import parse_cargo_deny_output


def test_parse_cargo_deny_output_single_diagnostic() -> None:
    """Parse a single cargo-deny diagnostic from JSON Lines."""
    output = (
        '{"type":"diagnostic","fields":{"severity":"error","code":"L001",'
        '"message":"license \'GPL-3.0\' is not allowed",'
        '"labels":[{"span":{"start":0,"end":10},"message":"crate foo"}]}}'
    )
    issues = parse_cargo_deny_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("Cargo.toml")
    assert_that(issues[0].code).is_equal_to("L001")
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[0].message).contains("license")
    assert_that(issues[0].crate_name).is_equal_to("foo")


def test_parse_cargo_deny_output_advisory() -> None:
    """Parse a security advisory from JSON Lines."""
    output = (
        '{"type":"advisory","fields":{"advisory":{"id":"RUSTSEC-2021-0001",'
        '"severity":"HIGH"},"versions":{"patched":[">=1.0.1"]},'
        '"package":{"name":"vulnerable-crate","version":"0.9.0"}}}'
    )
    issues = parse_cargo_deny_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].advisory_id).is_equal_to("RUSTSEC-2021-0001")
    assert_that(issues[0].advisory_severity).is_equal_to("HIGH")
    assert_that(issues[0].crate_name).is_equal_to("vulnerable-crate")
    assert_that(issues[0].crate_version).is_equal_to("0.9.0")
    assert_that(issues[0].patched_versions).contains(">=1.0.1")


def test_parse_cargo_deny_output_multiple_issues() -> None:
    """Parse multiple issues from JSON Lines."""
    output = (
        '{"type":"diagnostic","fields":{"severity":"error","code":"L001",'
        '"message":"license issue","labels":[{"message":"crate foo"}]}}\n'
        '{"type":"diagnostic","fields":{"severity":"warning","code":"B001",'
        '"message":"banned dependency","labels":[{"message":"crate bar@1.0.0"}]}}'
    )
    issues = parse_cargo_deny_output(output)
    assert_that(issues).is_length(2)
    assert_that(issues[0].crate_name).is_equal_to("foo")
    assert_that(issues[1].crate_name).is_equal_to("bar")
    assert_that(issues[1].crate_version).is_equal_to("1.0.0")


def test_parse_cargo_deny_output_ignores_non_issues() -> None:
    """Ignore non-issue types like summary."""
    output = (
        '{"type":"summary","fields":{"errors":1,"warnings":0}}\n'
        '{"type":"diagnostic","fields":{"severity":"error","code":"L001",'
        '"message":"license issue","labels":[{"message":"crate foo"}]}}'
    )
    issues = parse_cargo_deny_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("L001")


@pytest.mark.parametrize(
    ("output", "expected_count"),
    [
        pytest.param("", 0, id="empty_string"),
        pytest.param("\n\n", 0, id="only_newlines"),
        pytest.param(None, 0, id="none_input"),
    ],
)
def test_parse_cargo_deny_output_empty_cases(
    output: str | None,
    expected_count: int,
) -> None:
    """Handle empty output."""
    result = parse_cargo_deny_output(output or "")
    assert_that(result).is_length(expected_count)


def test_parse_cargo_deny_output_invalid_json() -> None:
    """Skip invalid JSON lines."""
    output = (
        '{"type":"diagnostic","fields":{"severity":"error","code":"L001",'
        '"message":"issue","labels":[{"message":"crate foo"}]}}\n'
        "not valid json\n"
        '{"type":"diagnostic","fields":{"severity":"warning","code":"B001",'
        '"message":"another issue","labels":[{"message":"crate bar"}]}}'
    )
    issues = parse_cargo_deny_output(output)
    assert_that(issues).is_length(2)


def test_parse_cargo_deny_output_crate_with_version() -> None:
    """Parse crate info with version from labels."""
    output = (
        '{"type":"diagnostic","fields":{"severity":"error","code":"D001",'
        '"message":"duplicate dependency",'
        '"labels":[{"message":"crate serde@1.0.150"}]}}'
    )
    issues = parse_cargo_deny_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].crate_name).is_equal_to("serde")
    assert_that(issues[0].crate_version).is_equal_to("1.0.150")
