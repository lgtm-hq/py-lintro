"""Unit tests for trufflehog parser handling of invalid input."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_parser import parse_trufflehog_output


def test_parse_none_input() -> None:
    """None input should return no issues."""
    assert_that(parse_trufflehog_output(output=None)).is_empty()


def test_parse_empty_string() -> None:
    """Empty string should return no issues."""
    assert_that(parse_trufflehog_output(output="")).is_empty()


def test_parse_whitespace_only() -> None:
    """Whitespace-only input should return no issues."""
    assert_that(parse_trufflehog_output(output="   \n\t  ")).is_empty()


def test_parse_invalid_json_line() -> None:
    """A non-JSON line should be skipped, not raise."""
    assert_that(parse_trufflehog_output(output="not valid json")).is_empty()


def test_parse_non_object_json() -> None:
    """A JSON array line (not an object) should be skipped."""
    assert_that(parse_trufflehog_output(output="[1, 2, 3]")).is_empty()
