"""Unit tests for Vale parser handling of invalid or empty input."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.vale.vale_parser import parse_vale_output


def test_none_input_returns_empty() -> None:
    """None input should yield an empty list."""
    assert_that(parse_vale_output(output=None)).is_empty()


def test_empty_string_returns_empty() -> None:
    """Empty string input should yield an empty list."""
    assert_that(parse_vale_output(output="")).is_empty()


def test_whitespace_only_returns_empty() -> None:
    """Whitespace-only input should yield an empty list."""
    assert_that(parse_vale_output(output="   \n\t  ")).is_empty()


def test_malformed_json_returns_empty() -> None:
    """Malformed JSON should yield an empty list rather than raising."""
    assert_that(parse_vale_output(output="{not valid json")).is_empty()


def test_non_object_root_returns_empty() -> None:
    """A JSON array root (not a mapping) should yield an empty list."""
    assert_that(parse_vale_output(output="[1, 2, 3]")).is_empty()


def test_no_config_error_object_returns_empty() -> None:
    """Vale's E100 runtime-error object should yield an empty list."""
    e100 = (
        '{"Line": 0, "Path": "", "Text": "E100 [.vale.ini not found] '
        'Runtime error", "Code": "E100", "Span": 0}'
    )

    assert_that(parse_vale_output(output=e100)).is_empty()


def test_non_list_alert_values_are_skipped() -> None:
    """Files whose value is not a list should be skipped safely."""
    output = '{"a.md": "not a list", "b.md": null}'

    assert_that(parse_vale_output(output=output)).is_empty()


def test_non_dict_alerts_are_skipped() -> None:
    """Non-dict entries within a file's alert list should be skipped."""
    output = '{"a.md": ["string", 42, null]}'

    assert_that(parse_vale_output(output=output)).is_empty()
