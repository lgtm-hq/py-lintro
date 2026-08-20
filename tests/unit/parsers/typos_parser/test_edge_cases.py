"""Tests for typos parser edge cases."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.typos.typos_parser import (
    parse_typos_errors,
    parse_typos_output,
)

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
    # An unusable byte_offset means "unknown", not "first character".
    assert_that(issues[0].byte_offset).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)


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


def test_boolean_location_values_do_not_leak_through() -> None:
    """JSON booleans decode to ``bool`` but must not become line/offset values."""
    output = (
        '{"type":"typo","path":"x.txt","line_num":true,'
        '"byte_offset":true,"typo":"teh","corrections":["the"]}'
    )

    issues = parse_typos_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].byte_offset).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)


def test_negative_location_values_fall_back_to_zero() -> None:
    """Out-of-range line/offset values never produce a non-positive column."""
    output = (
        '{"type":"typo","path":"x.txt","line_num":-3,'
        '"byte_offset":-5,"typo":"teh","corrections":["the"]}'
    )

    issues = parse_typos_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].byte_offset).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)


def test_zero_byte_offset_is_column_one() -> None:
    """A real zero offset is the first byte of the line, not "unknown"."""
    output = make_typo_record(line_num=4, byte_offset=0)

    issues = parse_typos_output(output)

    assert_that(issues[0].byte_offset).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(1)


def test_findings_without_corrections_are_not_fixable() -> None:
    """A word with no suggested correction cannot be auto-replaced."""
    output = make_typo_record(typo="asdfg", corrections=[])

    issues = parse_typos_output(output)

    assert_that(issues[0].fixable).is_false()


def test_findings_with_corrections_are_fixable() -> None:
    """A finding with a suggested correction is auto-fixable."""
    output = make_typo_record(typo="teh", corrections=["the"])

    issues = parse_typos_output(output)

    assert_that(issues[0].fixable).is_true()


def test_error_records_are_extracted_separately() -> None:
    """``error`` records are reported as diagnostics, not as findings."""
    output = "\n".join(
        [
            make_typo_record(path="good.txt"),
            '{"type":"error","path":"bad.txt","msg":"Permission denied"}',
        ],
    )

    assert_that(parse_typos_output(output)).is_length(1)
    assert_that(parse_typos_errors(output)).is_equal_to(
        ["bad.txt: Permission denied"],
    )


def test_error_records_without_a_path_still_report() -> None:
    """A pathless error record still yields a readable message."""
    output = '{"type":"error","msg":"config is invalid"}'

    assert_that(parse_typos_errors(output)).is_equal_to(["config is invalid"])


def test_no_error_records_yields_no_diagnostics() -> None:
    """Clean output produces no diagnostics."""
    assert_that(parse_typos_errors(make_typo_record())).is_empty()
    assert_that(parse_typos_errors(None)).is_empty()


def test_unknown_record_types_are_reported_as_diagnostics() -> None:
    """A record type typos might add later fails loudly instead of vanishing."""
    output = '{"type":"some_future_type","path":"x.txt","msg":"something odd"}'

    assert_that(parse_typos_output(output)).is_empty()
    assert_that(parse_typos_errors(output)).is_equal_to(["x.txt: something odd"])


def test_informational_record_types_are_not_diagnostics() -> None:
    """Known informational record types stay out of the diagnostics list."""
    output = '{"type":"binary_file","path":"logo.png"}'

    assert_that(parse_typos_output(output)).is_empty()
    assert_that(parse_typos_errors(output)).is_empty()


def test_undecodable_lines_are_reported_as_diagnostics() -> None:
    """With --format json, a non-JSON stdout line means something went wrong."""
    output = "\n".join([make_typo_record(), "argument `nosuch.txt` is not found"])

    assert_that(parse_typos_output(output)).is_length(1)
    assert_that(parse_typos_errors(output)).is_equal_to(
        ["unparseable typos output: argument `nosuch.txt` is not found"],
    )
