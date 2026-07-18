"""Tests for ktlint parser handling of empty, null, and malformed input."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.ktlint.ktlint_parser import parse_ktlint_output


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t"])
def test_parse_empty_or_none(value: str | None) -> None:
    """Empty or whitespace-only input returns an empty list.

    Args:
        value: The empty/None input to parse.
    """
    assert_that(parse_ktlint_output(value)).is_empty()


def test_parse_clean_empty_array() -> None:
    """Clean-run output (an empty array) yields no issues."""
    assert_that(parse_ktlint_output("[\n]")).is_empty()


def test_parse_invalid_json() -> None:
    """Malformed JSON returns an empty list rather than raising."""
    assert_that(parse_ktlint_output("not valid json")).is_empty()


def test_parse_non_list_root() -> None:
    """A JSON object root (not the expected array) returns an empty list."""
    assert_that(parse_ktlint_output('{"file": "a.kt"}')).is_empty()


def test_parse_skips_non_dict_entries() -> None:
    """Non-dict file entries are skipped without raising."""
    assert_that(parse_ktlint_output("[1, 2, 3]")).is_empty()


def test_parse_skips_non_list_errors() -> None:
    """A file entry whose ``errors`` is not a list is skipped."""
    assert_that(
        parse_ktlint_output('[{"file": "a.kt", "errors": "oops"}]'),
    ).is_empty()
