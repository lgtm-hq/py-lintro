"""Tests that the parser extracts fields from real typos JSON output."""

from __future__ import annotations

from assertpy import assert_that

from lintro.parsers.typos.typos_parser import parse_typos_output

from .conftest import make_typo_record, make_typos_output


def test_parse_single_typo(single_typo_output: str) -> None:
    """A single finding is parsed with all fields populated."""
    issues = parse_typos_output(single_typo_output)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("README.md")
    assert_that(issue.line).is_equal_to(3)
    # byte_offset 18 -> 1-based column 19.
    assert_that(issue.column).is_equal_to(19)
    assert_that(issue.byte_offset).is_equal_to(18)
    assert_that(issue.typo).is_equal_to("teh")
    assert_that(issue.corrections).is_equal_to(["the"])


def test_parse_multiple_typos(multi_typo_output: str) -> None:
    """Multiple newline-delimited findings are all parsed."""
    issues = parse_typos_output(multi_typo_output)

    assert_that(issues).is_length(3)
    assert_that([i.typo for i in issues]).is_equal_to(["teh", "seperate", "reprot"])
    assert_that([i.file for i in issues]).is_equal_to(["a.txt", "a.txt", "b.py"])


def test_message_composed_from_typo_and_corrections() -> None:
    """The message is composed from the typo and its corrections."""
    output = make_typo_record(typo="seperate", corrections=["separate"])

    issues = parse_typos_output(output)

    assert_that(issues[0].message).is_equal_to('"seperate" should be "separate"')


def test_message_lists_multiple_corrections() -> None:
    """Several corrections are comma-separated in the message."""
    output = make_typo_record(typo="wrods", corrections=["words", "words'"])

    issues = parse_typos_output(output)

    assert_that(issues[0].message).is_equal_to('"wrods" should be "words", "words\'"')


def test_column_is_one_based() -> None:
    """A zero byte offset maps to column 1."""
    output = make_typos_output(
        [make_typo_record(byte_offset=0, typo="reprot", corrections=["report"])],
    )

    issues = parse_typos_output(output)

    assert_that(issues[0].column).is_equal_to(1)
    assert_that(issues[0].byte_offset).is_equal_to(0)
