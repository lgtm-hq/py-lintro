"""Unit tests for Clippy parser."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.clippy.clippy_parser import parse_clippy_output


@pytest.mark.parametrize("value", [None, 42, False, [], {}])
def test_clippy_non_string_message_is_rejected(value: object) -> None:
    """A null or malformed message cannot manufacture a Clippy finding."""
    output = json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "code": {"code": "clippy::needless_return"},
                "message": value,
                "level": "warning",
                "spans": [{"file_name": "src/lib.rs", "line_start": 1}],
            },
        },
    )
    assert_that(parse_clippy_output(output)).is_empty()


def test_parse_clippy_output_single_issue() -> None:
    """Parse a single Clippy warning from JSON Lines."""
    output = (
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
        '"level":"warning","message":"unneeded `return` statement",'
        '"spans":[{"file_name":"src/lib.rs","line_start":42,"line_end":42,'
        '"column_start":5,"column_end":15}],'
        '"rendered":"warning: unneeded `return` statement..."}}'
    )
    issues = parse_clippy_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib.rs")
    assert_that(issues[0].line).is_equal_to(42)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].code).is_equal_to("clippy::needless_return")
    assert_that(issues[0].message).contains("unneeded")
    assert_that(issues[0].level).is_equal_to("warning")


def test_parse_clippy_output_multiple_issues() -> None:
    """Parse multiple Clippy warnings from JSON Lines."""
    output = (
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
        '"level":"warning","message":"unneeded return",'
        '"spans":[{"file_name":"src/lib.rs",'
        '"line_start":1,"line_end":1,"column_start":1,"column_end":10}]}}\n'
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::unused_variable"},'
        '"level":"warning","message":"unused variable",'
        '"spans":[{"file_name":"src/main.rs",'
        '"line_start":5,"line_end":5,"column_start":3,"column_end":8}]}}'
    )
    issues = parse_clippy_output(output)
    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("src/lib.rs")
    assert_that(issues[1].file).is_equal_to("src/main.rs")
    assert_that(issues[0].code).is_equal_to("clippy::needless_return")
    assert_that(issues[1].code).is_equal_to("clippy::unused_variable")


def test_parse_clippy_output_ignores_non_clippy() -> None:
    """Ignore non-Clippy compiler messages."""
    output = (
        '{"reason":"compiler-message","message":{"code":{"code":"E0425"},'
        '"level":"error","message":"cannot find value",'
        '"spans":[{"file_name":"src/lib.rs",'
        '"line_start":1,"line_end":1,"column_start":1,"column_end":5}]}}\n'
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
        '"level":"warning","message":"unneeded return",'
        '"spans":[{"file_name":"src/lib.rs",'
        '"line_start":2,"line_end":2,"column_start":1,"column_end":10}]}}'
    )
    issues = parse_clippy_output(output)
    # Should only parse the clippy issue, not the compiler error
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("clippy::needless_return")


def test_parse_clippy_output_ignores_non_compiler_messages() -> None:
    """Ignore non-compiler-message reasons."""
    output = (
        '{"reason":"build-finished","success":true}\n'
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
        '"level":"warning","message":"unneeded return",'
        '"spans":[{"file_name":"src/lib.rs",'
        '"line_start":1,"line_end":1,"column_start":1,"column_end":10}]}}'
    )
    issues = parse_clippy_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("clippy::needless_return")


def test_parse_clippy_output_empty() -> None:
    """Handle empty output."""
    assert_that(parse_clippy_output("")).is_empty()
    assert_that(parse_clippy_output("\n\n")).is_empty()


def test_parse_clippy_output_invalid_json() -> None:
    """Skip invalid JSON lines."""
    output = (
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
        '"level":"warning","message":"unneeded return",'
        '"spans":[{"file_name":"src/lib.rs",'
        '"line_start":1,"line_end":1,"column_start":1,"column_end":10}]}}\n'
        "not valid json\n"
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::unused_variable"},'
        '"level":"warning","message":"unused variable",'
        '"spans":[{"file_name":"src/main.rs",'
        '"line_start":5,"line_end":5,"column_start":3,"column_end":8}]}}'
    )
    issues = parse_clippy_output(output)
    assert_that(issues).is_length(2)


def test_parse_clippy_output_multi_line_span() -> None:
    """Handle multi-line spans correctly."""
    output = (
        '{"reason":"compiler-message","message":{"code":{"code":"clippy::too_many_lines"},'
        '"level":"warning","message":"function too long",'
        '"spans":[{"file_name":"src/lib.rs",'
        '"line_start":10,"line_end":15,"column_start":1,"column_end":5}]}}'
    )
    issues = parse_clippy_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(10)
    assert_that(issues[0].end_line).is_equal_to(15)


def test_parse_clippy_output_ansi_codes_stripped() -> None:
    """Strip ANSI escape codes from output for consistent CI/local parsing."""
    # Output with ANSI color codes (common in CI environments)
    output = (
        '\x1b[33m{"reason":"compiler-message","message":{"code":{"code":"clippy::needless_return"},'
        '"level":"warning","message":"unneeded `return` statement",'
        '"spans":[{"file_name":"src/lib.rs","line_start":42,"line_end":42,'
        '"column_start":5,"column_end":15}],'
        '"rendered":"warning: unneeded `return` statement..."}}\x1b[0m'
    )
    issues = parse_clippy_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib.rs")
    assert_that(issues[0].code).is_equal_to("clippy::needless_return")
