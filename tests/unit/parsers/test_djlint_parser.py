"""Unit tests for the djLint parser."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.djlint.djlint_parser import parse_djlint_output

# A realistic ``djlint --check`` output block for a single reformatted file.
_CHECK_OUTPUT = (
    "\n\n\ntemplates/page.jinja\n"
    "───────────────────────────────────────────────────────────────────────\n"
    "@@ -1,4 +1,4 @@\n\n"
    " <div>\n"
    '-<img src="logo.png">\n'
    '+    <img src="logo.png">\n'
    " </div>\n\n"
    "1 file would be updated.\n"
)

# A realistic ``djlint --lint`` output block with rule findings.
_LINT_OUTPUT = (
    "\nLinting 1/1 files\n\n\n"
    "templates/page.jinja\n"
    "───────────────────────────────────────────────────────────────────────\n"
    "H006 2:0 Img tag should have height and width attributes. "
    '<img src="logo.png">\n'
    "H013 2:0 Img tag should have an alt attribute. "
    '<img src="logo.png">\n\n'
    "Linted 1 file, found 2 errors.\n"
)


@pytest.mark.parametrize(
    "output",
    ["", None, "   \n  \n   "],
    ids=["empty", "none", "whitespace_only"],
)
def test_parse_returns_empty_for_no_content(output: str | None) -> None:
    """Parsing empty or whitespace-only output returns an empty list.

    Args:
        output: The djLint output to parse.
    """
    assert_that(parse_djlint_output(output)).is_empty()


def test_parse_check_output_single_file() -> None:
    """A single-file check diff yields one fixable formatting issue."""
    issues = parse_djlint_output(_CHECK_OUTPUT)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("templates/page.jinja")
    assert_that(issue.code).is_equal_to("")
    assert_that(issue.fixable).is_true()
    assert_that(issue.message).is_equal_to("File would be reformatted")


def test_parse_check_output_multiple_files() -> None:
    """Multiple file headers yield one formatting issue per file."""
    output = (
        "\n\na.jinja\n"
        "──────────────────────────────\n"
        "@@ -1 +1 @@\n"
        "-<div>\n"
        "+  <div>\n\n"
        "b.jinja\n"
        "──────────────────────────────\n"
        "@@ -1 +1 @@\n"
        "-<p>\n"
        "+  <p>\n\n"
        "2 files would be updated.\n"
    )
    issues = parse_djlint_output(output)

    assert_that(issues).is_length(2)
    assert_that([i.file for i in issues]).contains("a.jinja", "b.jinja")
    assert_that([i.fixable for i in issues]).contains_only(True)


def test_parse_lint_output_takes_precedence() -> None:
    """Rule-based lint findings are parsed with codes and marked non-fixable."""
    issues = parse_djlint_output(_LINT_OUTPUT)

    assert_that(issues).is_length(2)
    codes = [i.code for i in issues]
    assert_that(codes).contains("H006", "H013")
    for issue in issues:
        assert_that(issue.line).is_equal_to(2)
        assert_that(issue.column).is_equal_to(0)
        assert_that(issue.fixable).is_false()
        assert_that(issue.file).is_equal_to("")


def test_parse_lint_line_with_leading_file() -> None:
    """The file-first linter output format is parsed correctly."""
    output = "templates/page.jinja 15:4 H013 Img tag should have an alt attribute."
    issues = parse_djlint_output(output)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("templates/page.jinja")
    assert_that(issue.line).is_equal_to(15)
    assert_that(issue.column).is_equal_to(4)
    assert_that(issue.code).is_equal_to("H013")
    assert_that(issue.fixable).is_false()


def test_parse_summary_fallback_without_headers() -> None:
    """A summary-only output synthesizes fixable issues from the count."""
    output = "Checking 3/3 files\n\n3 files would be updated.\n"
    issues = parse_djlint_output(output)

    assert_that(issues).is_length(3)
    assert_that([i.file for i in issues]).contains_only("<unknown>")
    assert_that([i.fixable for i in issues]).contains_only(True)


def test_parse_clean_check_output_returns_empty() -> None:
    """Clean check output (no files to update) yields no issues."""
    output = "Checking 1/1 files\n\n0 files would be updated.\n"
    assert_that(parse_djlint_output(output)).is_empty()


def test_parse_malformed_lines_are_ignored() -> None:
    """Lines that match no known shape are ignored without raising."""
    output = "this is not djlint output\nrandom text 123\n:::\n"
    assert_that(parse_djlint_output(output)).is_empty()


def test_parse_status_header_not_treated_as_file() -> None:
    """A status banner preceding a rule must not become a formatting issue."""
    output = "Checking 1/1 files\n" "──────────────────────────────\n" "@@ -1 +1 @@\n"
    # The only line before the rule is a status banner, so no file is emitted;
    # with no summary line either, the result is empty.
    assert_that(parse_djlint_output(output)).is_empty()


def test_display_row_for_formatting_issue() -> None:
    """A formatting issue renders a display row marked fixable."""
    issues = parse_djlint_output(_CHECK_OUTPUT)
    row = issues[0].to_display_row()

    assert_that(row["file"]).is_equal_to("templates/page.jinja")
    assert_that(row["fixable"]).is_equal_to("Yes")
