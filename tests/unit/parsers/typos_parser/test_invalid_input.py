"""Tests for empty, null, and malformed typos input."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.typos.typos_parser import parse_typos_output


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(None, id="none_input"),
        pytest.param("", id="empty_string"),
        pytest.param("   \n\n  \t", id="whitespace_only"),
    ],
)
def test_empty_inputs_return_no_issues(output: str | None) -> None:
    """Empty or None input yields an empty issue list."""
    assert_that(parse_typos_output(output)).is_length(0)


def test_malformed_json_lines_are_skipped() -> None:
    """Non-JSON lines are ignored rather than raising."""
    output = "not json at all\n{ broken\n"

    assert_that(parse_typos_output(output)).is_length(0)


def test_non_object_json_is_skipped() -> None:
    """JSON that is not an object (e.g. an array) is ignored."""
    output = '[1, 2, 3]\n42\n"a string"'

    assert_that(parse_typos_output(output)).is_length(0)


def test_non_typo_records_are_ignored() -> None:
    """Only ``type == "typo"`` records produce issues."""
    output = (
        '{"type":"error","message":"boom"}\n{"type":"binary_file","path":"a.bin"}\n'
    )

    assert_that(parse_typos_output(output)).is_length(0)


def test_typo_record_missing_required_fields_is_skipped() -> None:
    """A ``typo`` record without path or typo text is skipped."""
    output = '{"type":"typo","line_num":1}'

    assert_that(parse_typos_output(output)).is_length(0)


def test_valid_and_invalid_lines_mixed() -> None:
    """Valid findings are kept even when interleaved with junk lines."""
    output = (
        "garbage\n"
        '{"type":"typo","path":"x.txt","line_num":1,"byte_offset":0,'
        '"typo":"teh","corrections":["the"]}\n'
        "{ also broken\n"
    )

    issues = parse_typos_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].typo).is_equal_to("teh")
