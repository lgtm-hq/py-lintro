"""Unit tests for the buf parser.

Covers ``parse_buf_output`` (line-delimited JSON lint violations) and
``parse_buf_format_output`` (unified-diff format findings). All fixtures use
output captured from a real ``buf 1.71.0`` run.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.buf.buf_issue import BufIssue
from lintro.parsers.buf.buf_parser import (
    parse_buf_format_output,
    parse_buf_output,
)

# A single real lint violation line from ``buf lint --error-format json``.
_LINT_LINE = (
    '{"path":"v.proto","start_line":2,"start_column":1,"end_line":2,'
    '"end_column":19,"type":"PACKAGE_LOWER_SNAKE_CASE",'
    '"message":"Package name \\"MyPackage\\" should be lower_snake.case, '
    'such as \\"my_package\\"."}'
)


@pytest.mark.parametrize(
    "output",
    [None, "", "   \n  \n   "],
    ids=["none", "empty", "whitespace_only"],
)
def test_parse_buf_output_returns_empty_for_no_content(output: str | None) -> None:
    """Empty, None, or whitespace-only output yields no issues.

    Args:
        output: The buf output to parse.
    """
    assert_that(parse_buf_output(output)).is_empty()


def test_parse_buf_output_extracts_all_fields() -> None:
    """A single JSON violation maps every field onto the issue."""
    result = parse_buf_output(_LINT_LINE)

    assert_that(result).is_length(1)
    issue = result[0]
    assert_that(issue.file).is_equal_to("v.proto")
    assert_that(issue.line).is_equal_to(2)
    assert_that(issue.column).is_equal_to(1)
    assert_that(issue.end_line).is_equal_to(2)
    assert_that(issue.end_column).is_equal_to(19)
    assert_that(issue.code).is_equal_to("PACKAGE_LOWER_SNAKE_CASE")
    assert_that(issue.level).is_equal_to("error")
    assert_that(issue.message).contains("lower_snake.case")


def test_parse_buf_output_multiple_lines() -> None:
    """Newline-delimited JSON objects each become an issue."""
    output = "\n".join(
        [
            '{"path":"a.proto","start_line":1,"start_column":1,"end_line":1,'
            '"end_column":5,"type":"PACKAGE_DIRECTORY_MATCH","message":"m1"}',
            '{"path":"b.proto","start_line":3,"start_column":2,"end_line":3,'
            '"end_column":8,"type":"MESSAGE_PASCAL_CASE","message":"m2"}',
        ],
    )
    result = parse_buf_output(output)

    assert_that(result).is_length(2)
    assert_that(result[0].file).is_equal_to("a.proto")
    assert_that(result[0].code).is_equal_to("PACKAGE_DIRECTORY_MATCH")
    assert_that(result[1].file).is_equal_to("b.proto")
    assert_that(result[1].line).is_equal_to(3)


def test_parse_buf_output_compile_error() -> None:
    """Compile/parse errors surface with the COMPILE rule id."""
    output = (
        '{"path":"broken.proto","start_line":2,"start_column":1,"end_line":3,'
        '"end_column":1,"type":"COMPILE","message":"missing name after `message`"}'
    )
    result = parse_buf_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("COMPILE")
    assert_that(result[0].message).contains("missing name")


@pytest.mark.parametrize(
    "line",
    [
        "not json at all",
        "{malformed json",
        "[1, 2, 3]",
        '{"no_path":true}',
        "",
    ],
    ids=["plain_text", "broken_brace", "json_array", "missing_path", "blank"],
)
def test_parse_buf_output_skips_unparseable_lines(line: str) -> None:
    """Malformed or non-violation lines are skipped, not raised.

    Args:
        line: A line that must not produce an issue.
    """
    assert_that(parse_buf_output(line)).is_empty()


def test_parse_buf_output_skips_bad_lines_but_keeps_good_ones() -> None:
    """A malformed line does not discard sibling valid violations."""
    output = "\n".join(["garbage line", _LINT_LINE, "{still bad"])
    result = parse_buf_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("PACKAGE_LOWER_SNAKE_CASE")


def test_parse_buf_output_missing_positions_default_to_zero() -> None:
    """Absent or non-numeric position fields default to 0."""
    output = '{"path":"v.proto","type":"SOME_RULE","message":"m"}'
    result = parse_buf_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(0)
    assert_that(result[0].column).is_equal_to(0)
    assert_that(result[0].end_line).is_equal_to(0)
    assert_that(result[0].end_column).is_equal_to(0)


def test_parse_buf_output_unicode_message() -> None:
    """Unicode characters in messages are preserved."""
    output = (
        '{"path":"v.proto","start_line":1,"start_column":1,"end_line":1,'
        '"end_column":2,"type":"R","message":"caractère invalide"}'
    )
    result = parse_buf_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].message).contains("invalide")


def test_parse_buf_output_ansi_codes_stripped() -> None:
    """ANSI escape sequences are stripped before JSON parsing."""
    output = f"\x1b[31m{_LINT_LINE}\x1b[0m"
    result = parse_buf_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("v.proto")


# =============================================================================
# Format diff output (buf format -d)
# =============================================================================


@pytest.mark.parametrize(
    "output",
    [None, "", "   "],
    ids=["none", "empty", "whitespace"],
)
def test_parse_buf_format_output_empty(output: str | None) -> None:
    """Empty diff output yields no format issues.

    Args:
        output: The diff output to parse.
    """
    assert_that(parse_buf_format_output(output)).is_empty()


def test_parse_buf_format_output_single_file() -> None:
    """A diff for one file produces one FORMAT issue for that file."""
    output = (
        "diff -u f.proto.orig f.proto\n"
        "--- f.proto.orig\t2026-07-07 02:47:43\n"
        "+++ f.proto\t2026-07-07 02:47:43\n"
        "@@ -1,3 +1,2 @@\n"
        '-syntax="proto3";\n'
        '+syntax = "proto3";\n'
    )
    result = parse_buf_format_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("f.proto")
    assert_that(result[0].code).is_equal_to("FORMAT")
    assert_that(result[0].level).is_equal_to("error")


def test_parse_buf_format_output_multiple_files() -> None:
    """A diff spanning several files yields one issue per file."""
    output = (
        "diff -u a.proto.orig a.proto\n"
        "--- a.proto.orig\t2026-07-07\n"
        "+++ a.proto\t2026-07-07\n"
        "@@ -1 +1 @@\n"
        "diff -u sub/b.proto.orig sub/b.proto\n"
        "--- sub/b.proto.orig\t2026-07-07\n"
        "+++ sub/b.proto\t2026-07-07\n"
        "@@ -1 +1 @@\n"
    )
    result = parse_buf_format_output(output)

    assert_that(result).is_length(2)
    assert_that([i.file for i in result]).is_equal_to(["a.proto", "sub/b.proto"])


def test_parse_buf_format_output_deduplicates_paths() -> None:
    """Repeated target headers for the same file collapse to one issue."""
    output = (
        "+++ f.proto\t2026-07-07\n"
        "@@ -1 +1 @@\n"
        "+++ f.proto\t2026-07-07\n"
    )
    result = parse_buf_format_output(output)

    assert_that(result).is_length(1)


def test_buf_issue_defaults() -> None:
    """BufIssue exposes buf-specific fields with sane defaults."""
    issue = BufIssue(file="x.proto", line=1, column=2, message="m")

    assert_that(issue.level).is_equal_to("error")
    assert_that(issue.code).is_equal_to("")
    assert_that(issue.end_line).is_equal_to(0)
    assert_that(issue.end_column).is_equal_to(0)
