"""Unit tests for the dotenv-linter parser.

These tests exercise the parser against output captured from a real
dotenv-linter v4.0.0 run, so the fixtures reflect the tool's actual format.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.dotenv_linter.dotenv_linter_issue import DotenvLinterIssue
from lintro.parsers.dotenv_linter.dotenv_linter_parser import (
    parse_dotenv_linter_output,
)

# Real ``dotenv-linter check --plain`` output for a file with several issues.
REAL_CHECK_OUTPUT = """Checking .env
.env:2 LowercaseKey: The foo key should be in uppercase
.env:3 KeyWithoutValue: The BAR key should be with a value or have an equal sign
.env:3 UnorderedKey: The BAR key should go before the FOO key
.env:4 LeadingCharacter: Invalid leading character detected
.env:4 UnorderedKey: The BAZ key should go before the FOO key
.env:5 SpaceCharacter: The line has spaces around equal sign
.env:5 UnorderedKey: The ABC  key should go before the BAR key

Found 7 problems
"""

# Real output for a file with no issues.
REAL_CLEAN_OUTPUT = "Checking .env\n\nNo problems found\n"


@pytest.mark.parametrize(
    "output",
    [None, "", "   \n  \n   "],
    ids=["none", "empty", "whitespace_only"],
)
def test_parse_returns_empty_for_no_content(output: str | None) -> None:
    """Parser returns an empty list for None, empty, or whitespace input.

    Args:
        output: The dotenv-linter output to parse.
    """
    result = parse_dotenv_linter_output(output)
    assert_that(result).is_empty()


def test_parse_clean_output_returns_empty() -> None:
    """Parser ignores header and summary lines for a clean run."""
    result = parse_dotenv_linter_output(REAL_CLEAN_OUTPUT)
    assert_that(result).is_empty()


def test_parse_real_output_issue_count() -> None:
    """Parser extracts every diagnostic line from real output."""
    result = parse_dotenv_linter_output(REAL_CHECK_OUTPUT)
    assert_that(result).is_length(7)


def test_parse_ignores_header_and_summary_lines() -> None:
    """Header ('Checking') and summary ('Found N problems') are skipped."""
    result = parse_dotenv_linter_output(REAL_CHECK_OUTPUT)
    codes = [issue.code for issue in result]
    assert_that(codes).does_not_contain("Checking")
    assert_that(codes).does_not_contain("Found")


def test_parse_single_issue_field_extraction() -> None:
    """Parser extracts file, line, code, and message from a single line."""
    output = ".env:2 LowercaseKey: The foo key should be in uppercase"
    result = parse_dotenv_linter_output(output)

    assert_that(result).is_length(1)
    issue = result[0]
    assert_that(issue).is_instance_of(DotenvLinterIssue)
    assert_that(issue.file).is_equal_to(".env")
    assert_that(issue.line).is_equal_to(2)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.code).is_equal_to("LowercaseKey")
    assert_that(issue.message).is_equal_to("The foo key should be in uppercase")


def test_parse_preserves_colons_in_message() -> None:
    """A message containing a colon is preserved after the check name."""
    output = ".env:7 IncorrectDelimiter: The FOO-BAR key has: an incorrect delimiter"
    result = parse_dotenv_linter_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("IncorrectDelimiter")
    assert_that(result[0].message).is_equal_to(
        "The FOO-BAR key has: an incorrect delimiter",
    )


def test_parse_handles_path_with_directories() -> None:
    """File paths with directory separators are captured intact."""
    output = "config/.env.local:1 DuplicatedKey: The FOO key is duplicated"
    result = parse_dotenv_linter_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("config/.env.local")
    assert_that(result[0].line).is_equal_to(1)


def test_parse_strips_ansi_color_codes() -> None:
    """ANSI-colored output is normalized before matching."""
    output = "\x1b[1m.env\x1b[0m:2 LowercaseKey: The foo key should be in uppercase"
    result = parse_dotenv_linter_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to(".env")
    assert_that(result[0].code).is_equal_to("LowercaseKey")


def test_parse_multiple_issues_same_line() -> None:
    """Two diagnostics on the same source line are both captured."""
    output = (
        ".env:3 KeyWithoutValue: The BAR key should be with a value\n"
        ".env:3 UnorderedKey: The BAR key should go before the FOO key"
    )
    result = parse_dotenv_linter_output(output)

    assert_that(result).is_length(2)
    assert_that([i.line for i in result]).is_equal_to([3, 3])
    assert_that([i.code for i in result]).is_equal_to(
        ["KeyWithoutValue", "UnorderedKey"],
    )


def test_issue_model_defaults() -> None:
    """A DotenvLinterIssue defaults to a fixable warning."""
    issue = DotenvLinterIssue(
        file=".env",
        line=1,
        code="LowercaseKey",
        message="msg",
    )
    assert_that(issue.level).is_equal_to("warning")
    assert_that(issue.fixable).is_true()


def test_issue_model_display_row_maps_level_to_severity() -> None:
    """to_display_row exposes the check name and level-derived severity."""
    issue = DotenvLinterIssue(
        file=".env",
        line=2,
        code="LowercaseKey",
        message="The foo key should be in uppercase",
    )
    row = issue.to_display_row()
    assert_that(row["code"]).is_equal_to("LowercaseKey")
    assert_that(row["severity"]).is_equal_to("WARNING")
    assert_that(row["fixable"]).is_equal_to("Yes")
    assert_that(row["column"]).is_equal_to("-")


def test_parse_skips_unrecognized_lines() -> None:
    """Lines that do not match the diagnostic pattern are ignored."""
    output = (
        "some random noise\n"
        ".env:2 LowercaseKey: The foo key should be in uppercase\n"
        "Dry run - not changing any files on disk.\n"
    )
    result = parse_dotenv_linter_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("LowercaseKey")
