"""Unit tests for oxfmt parser."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.oxfmt.oxfmt_parser import parse_oxfmt_output


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
    ],
    ids=["none", "empty"],
)
def test_parse_oxfmt_output_returns_empty_for_no_content(
    output: str | None,
) -> None:
    """Parse empty or None output returns empty list.

    Args:
        output: The oxfmt output to parse.
    """
    result = parse_oxfmt_output(output)
    assert_that(result).is_empty()


def test_parse_oxfmt_output_single_file() -> None:
    """Parse single file path into one issue."""
    output = "src/file1.js"
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("src/file1.js")
    assert_that(result[0].line).is_equal_to(1)
    assert_that(result[0].column).is_equal_to(1)
    assert_that(result[0].message).is_equal_to("File is not formatted")
    assert_that(result[0].code).is_equal_to("FORMAT")


def test_parse_oxfmt_output_multiple_files() -> None:
    """Parse multiple file paths into multiple issues."""
    output = """src/file1.js
src/file2.ts
src/components/Button.tsx"""
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(3)
    assert_that(result[0].file).is_equal_to("src/file1.js")
    assert_that(result[1].file).is_equal_to("src/file2.ts")
    assert_that(result[2].file).is_equal_to("src/components/Button.tsx")


def test_parse_oxfmt_output_empty_lines_handling() -> None:
    """Empty lines are ignored."""
    output = """src/a.js

src/b.js

"""
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(2)
    assert_that(result[0].file).is_equal_to("src/a.js")
    assert_that(result[1].file).is_equal_to("src/b.js")


def test_parse_oxfmt_output_whitespace_handling() -> None:
    """Whitespace around file paths is stripped."""
    output = """  src/file1.js
	src/file2.ts
   src/file3.jsx   """
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(3)
    assert_that(result[0].file).is_equal_to("src/file1.js")
    assert_that(result[1].file).is_equal_to("src/file2.ts")
    assert_that(result[2].file).is_equal_to("src/file3.jsx")


def test_parse_oxfmt_output_whitespace_only_lines() -> None:
    """Lines with only whitespace are ignored."""
    output = """src/file.js


src/other.js"""
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(2)


def test_parse_oxfmt_output_ansi_escape_codes_stripped() -> None:
    """ANSI escape codes are stripped from output."""
    output = "\x1b[33msrc/file.js\x1b[39m"
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("src/file.js")


def test_parse_oxfmt_output_default_line_and_column() -> None:
    """Default line and column are 1."""
    output = "src/file.js"
    result = parse_oxfmt_output(output)
    assert_that(result[0].line).is_equal_to(1)
    assert_that(result[0].column).is_equal_to(1)


def test_parse_oxfmt_output_deeply_nested_path() -> None:
    """Handle deeply nested file paths."""
    deep_path = "a/b/c/d/e/f/g/h/i/j/component.tsx"
    result = parse_oxfmt_output(deep_path)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to(deep_path)


def test_parse_oxfmt_output_various_extensions() -> None:
    """Handle various JavaScript/TypeScript/Vue file extensions."""
    output = """file.js
file.mjs
file.cjs
file.jsx
file.ts
file.mts
file.cts
file.tsx
file.vue"""
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(9)
    extensions = [issue.file.split(".")[-1] for issue in result]
    assert_that(extensions).contains(
        "js",
        "mjs",
        "cjs",
        "jsx",
        "ts",
        "mts",
        "cts",
        "tsx",
        "vue",
    )


def test_parse_oxfmt_output_file_with_spaces_in_name() -> None:
    """Handle file paths with spaces in name."""
    output = "src/my component.js"
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("src/my component.js")


def test_parse_oxfmt_output_filters_error_messages() -> None:
    """Error messages from oxfmt are filtered out."""
    output = """Expected at least one target file
src/file.js
error: Something went wrong
src/other.ts"""
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(2)
    assert_that(result[0].file).is_equal_to("src/file.js")
    assert_that(result[1].file).is_equal_to("src/other.ts")


def test_parse_oxfmt_output_filters_non_supported_extensions() -> None:
    """Files with unsupported extensions are filtered out."""
    output = """src/file.js
src/styles.css
src/config.json
src/readme.md
src/component.tsx"""
    result = parse_oxfmt_output(output)
    # Only .js and .tsx in the test data are valid oxfmt extensions
    # (.css, .json, .md are not)
    assert_that(result).is_length(2)
    assert_that(result[0].file).is_equal_to("src/file.js")
    assert_that(result[1].file).is_equal_to("src/component.tsx")


def test_parse_oxfmt_output_filters_warning_messages() -> None:
    """Warning messages from oxfmt are filtered out."""
    output = """WARNING: Some warning
src/file.ts
Warning: Another warning
warning: lowercase warning"""
    result = parse_oxfmt_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("src/file.ts")
