"""Unit tests for the Markdownlint output parser.

These tests validate that the parser handles empty output and typical
markdownlint-cli2 default formatter lines, producing structured issues.
"""

from assertpy import assert_that

from lintro.parsers.markdownlint.markdownlint_parser import parse_markdownlint_output


def test_parse_markdownlint_empty() -> None:
    """Return an empty list for empty parser input."""
    assert_that(parse_markdownlint_output("")).is_equal_to([])
    assert_that(parse_markdownlint_output("   ")).is_equal_to([])


def test_parse_markdownlint_lines() -> None:
    """Parse typical markdownlint-cli2 lines and produce structured issues."""
    out = (
        "dir/about.md:1:1 MD021/no-multiple-space-closed-atx Multiple spaces "
        'inside hashes on closed atx style heading [Context: "#  About  #"]\n'
        "dir/about.md:4 MD032/blanks-around-lists Lists should be surrounded "
        'by blank lines [Context: "1. List"]\n'
        "viewme.md:3:10 MD009/no-trailing-spaces Trailing spaces "
        "[Expected: 0 or 2; Actual: 1]"
    )
    issues = parse_markdownlint_output(out)
    assert_that(len(issues)).is_equal_to(3)

    i0 = issues[0]
    assert_that(i0.file).is_equal_to("dir/about.md")
    assert_that(i0.line).is_equal_to(1)
    assert_that(i0.column).is_equal_to(1)
    assert_that(i0.code).is_equal_to("MD021")
    assert_that(i0.message).contains("Multiple spaces")

    i1 = issues[1]
    assert_that(i1.file).is_equal_to("dir/about.md")
    assert_that(i1.line).is_equal_to(4)
    assert_that(i1.column).is_equal_to(0)  # 0 means unknown/not provided
    assert_that(i1.code).is_equal_to("MD032")
    assert_that(i1.message).contains("Lists should be surrounded")

    i2 = issues[2]
    assert_that(i2.file).is_equal_to("viewme.md")
    assert_that(i2.line).is_equal_to(3)
    assert_that(i2.column).is_equal_to(10)
    assert_that(i2.code).is_equal_to("MD009")
    assert_that(i2.message).contains("Trailing spaces")


def test_parse_markdownlint_without_column() -> None:
    """Parse markdownlint output without column information."""
    out = "file.md:5 MD041/first-line-heading First line should be a heading"
    issues = parse_markdownlint_output(out)
    assert_that(len(issues)).is_equal_to(1)
    i0 = issues[0]
    assert_that(i0.file).is_equal_to("file.md")
    assert_that(i0.line).is_equal_to(5)
    assert_that(i0.column).is_equal_to(0)  # 0 means unknown/not provided
    assert_that(i0.code).is_equal_to("MD041")
    assert_that(i0.message).contains("First line should be a heading")


def test_parse_markdownlint_ignores_malformed_lines() -> None:
    """Ignore lines that don't match the expected format."""
    out = (
        "file.md:1:1 MD013/line-length Line too long\n"
        "This is not a valid markdownlint line\n"
        "file.md:3 MD012/no-multiple-blanks Multiple blank lines"
    )
    issues = parse_markdownlint_output(out)
    assert_that(len(issues)).is_equal_to(2)
    assert_that(issues[0].code).is_equal_to("MD013")
    assert_that(issues[1].code).is_equal_to("MD012")


def test_parse_markdownlint_multiline_messages() -> None:
    """Parse markdownlint output with multi-line messages (continuation lines)."""
    out = (
        "dir/about.md:1:1 MD021/no-multiple-space-closed-atx Multiple spaces\n"
        '    inside hashes on closed atx style heading [Context: "#  About  #"]\n'
        "dir/about.md:4 MD032/blanks-around-lists Lists should be surrounded\n"
        '    by blank lines [Context: "1. List"]\n'
        "viewme.md:3:10 MD009/no-trailing-spaces Trailing spaces\n"
        "    [Expected: 0 or 2; Actual: 1]"
    )
    issues = parse_markdownlint_output(out)
    assert_that(len(issues)).is_equal_to(3)

    i0 = issues[0]
    assert_that(i0.file).is_equal_to("dir/about.md")
    assert_that(i0.line).is_equal_to(1)
    assert_that(i0.column).is_equal_to(1)
    assert_that(i0.code).is_equal_to("MD021")
    assert_that(i0.message).contains("Multiple spaces")
    assert_that(i0.message).contains("inside hashes")

    i1 = issues[1]
    assert_that(i1.file).is_equal_to("dir/about.md")
    assert_that(i1.line).is_equal_to(4)
    assert_that(i1.column).is_equal_to(0)  # 0 means unknown/not provided
    assert_that(i1.code).is_equal_to("MD032")
    assert_that(i1.message).contains("Lists should be surrounded")
    assert_that(i1.message).contains("by blank lines")

    i2 = issues[2]
    assert_that(i2.file).is_equal_to("viewme.md")
    assert_that(i2.line).is_equal_to(3)
    assert_that(i2.column).is_equal_to(10)
    assert_that(i2.code).is_equal_to("MD009")
    assert_that(i2.message).contains("Trailing spaces")


def test_parse_markdownlint_multiline_with_empty_lines() -> None:
    """Parse markdownlint output with multi-line messages separated by empty lines."""
    out = (
        "file.md:1:1 MD013/line-length Line too long\n"
        "    continuation part one\n"
        "    continuation part two\n"
        "\n"
        "file.md:3 MD012/no-multiple-blanks Multiple blank lines"
    )
    issues = parse_markdownlint_output(out)
    assert_that(len(issues)).is_equal_to(2)

    i0 = issues[0]
    assert_that(i0.file).is_equal_to("file.md")
    assert_that(i0.line).is_equal_to(1)
    assert_that(i0.code).is_equal_to("MD013")
    assert_that(i0.message).contains("Line too long")
    assert_that(i0.message).contains("continuation part one")
    assert_that(i0.message).contains("continuation part two")

    i1 = issues[1]
    assert_that(i1.file).is_equal_to("file.md")
    assert_that(i1.line).is_equal_to(3)
    assert_that(i1.code).is_equal_to("MD012")


def test_parse_markdownlint_ansi_codes_stripped() -> None:
    """Strip ANSI escape codes from output for consistent CI/local parsing."""
    # Output with ANSI color codes (common in CI environments)
    output = (
        "\x1b[31mdir/about.md:1:1 MD021/no-multiple-space-closed-atx"
        " Multiple spaces\x1b[0m"
    )
    issues = parse_markdownlint_output(output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].file).is_equal_to("dir/about.md")
    assert_that(issues[0].code).is_equal_to("MD021")
