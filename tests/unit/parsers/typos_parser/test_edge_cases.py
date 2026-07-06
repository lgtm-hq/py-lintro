"""Tests for typos parser edge cases."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.typos.typos_parser import parse_typos_output

from .conftest import make_typo_record


def test_typo_without_corrections() -> None:
    """A finding with no corrections still parses with a helpful message."""
    output = make_typo_record(typo="asdfg", corrections=[])

    issues = parse_typos_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].corrections).is_equal_to([])
    assert_that(issues[0].message).is_equal_to('"asdfg" is disallowed')


def test_unicode_typo_is_preserved() -> None:
    """Unicode content in the typo and file path is preserved."""
    output = make_typo_record(
        path="café/naïve.md",
        typo="téh",
        corrections=["the"],
    )

    issues = parse_typos_output(output)

    assert_that(issues[0].file).is_equal_to("café/naïve.md")
    assert_that(issues[0].typo).is_equal_to("téh")


def test_non_integer_location_defaults_to_zero() -> None:
    """Non-integer line/offset values fall back to zero without raising."""
    output = (
        '{"type":"typo","path":"x.txt","line_num":"oops",'
        '"byte_offset":null,"typo":"teh","corrections":["the"]}'
    )

    issues = parse_typos_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)
    # byte_offset falls back to 0 -> column 1.
    assert_that(issues[0].column).is_equal_to(1)


def test_corrections_coerced_to_strings() -> None:
    """Non-string correction entries are coerced to strings."""
    output = (
        '{"type":"typo","path":"x.txt","line_num":1,"byte_offset":0,'
        '"typo":"teh","corrections":[1,2]}'
    )

    issues = parse_typos_output(output)

    assert_that(issues[0].corrections).is_equal_to(["1", "2"])


def test_missing_corrections_key_defaults_to_empty() -> None:
    """A record without a corrections key yields an empty corrections list."""
    output = '{"type":"typo","path":"x.txt","line_num":1,"byte_offset":0,"typo":"teh"}'

    issues = parse_typos_output(output)

    assert_that(issues[0].corrections).is_equal_to([])
