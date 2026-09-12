"""Tests for RuboCop parser handling of empty and malformed input."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.rubocop.rubocop_parser import parse_rubocop_output


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t"])
def test_empty_or_null_input(value: str | None) -> None:
    """Empty, whitespace, or null input yields no issues."""
    assert_that(parse_rubocop_output(value)).is_empty()


def test_malformed_json() -> None:
    """Malformed JSON is handled gracefully (no exception, empty list)."""
    assert_that(parse_rubocop_output("{not valid json")).is_empty()


def test_json_array_instead_of_object() -> None:
    """A JSON array (wrong top-level type) yields no issues."""
    assert_that(parse_rubocop_output("[1, 2, 3]")).is_empty()


def test_missing_files_key() -> None:
    """A JSON object without a ``files`` list yields no issues."""
    assert_that(parse_rubocop_output('{"summary": {"offense_count": 0}}')).is_empty()


def test_files_not_a_list() -> None:
    """A non-list ``files`` value yields no issues."""
    assert_that(parse_rubocop_output('{"files": {}}')).is_empty()


def test_offenses_not_a_list() -> None:
    """A file entry with a non-list ``offenses`` value is skipped."""
    output = '{"files": [{"path": "a.rb", "offenses": "oops"}]}'
    assert_that(parse_rubocop_output(output)).is_empty()


def test_no_offenses_returns_empty() -> None:
    """A clean file (empty offenses list) yields no issues."""
    output = '{"files": [{"path": "clean.rb", "offenses": []}], "summary": {}}'
    assert_that(parse_rubocop_output(output)).is_empty()


def test_trailing_diagnostics_after_json() -> None:
    """JSON followed by trailing stderr-style noise still parses cleanly."""
    output = (
        '{"files": [{"path": "a.rb", "offenses": '
        '[{"cop_name": "Lint/Void", "severity": "warning", '
        '"message": "x", "correctable": true, '
        '"location": {"start_line": 1, "start_column": 1}}]}]}\n'
        "The following cops were added to RuboCop, but are not configured."
    )
    issues = parse_rubocop_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("Lint/Void")
