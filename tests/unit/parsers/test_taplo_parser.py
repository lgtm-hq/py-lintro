"""Unit tests for taplo parser."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.taplo.taplo_parser import parse_taplo_output


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
        "   \n  \n   ",
    ],
    ids=["none", "empty", "whitespace_only"],
)
def test_parse_taplo_output_returns_empty_for_no_content(output: str | None) -> None:
    """Parse empty, None, or whitespace-only output returns empty list.

    Args:
        output: The taplo output to parse.
    """
    result = parse_taplo_output(output)
    assert_that(result).is_empty()


def test_parse_taplo_output_extracts_all_fields() -> None:
    """Parse error-level issue extracts all fields correctly."""
    output = """error[invalid_value]: invalid value
  --> pyproject.toml:5:10
   |
 5 | version =
   |          ^ expected a value"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("pyproject.toml")
    assert_that(result[0].line).is_equal_to(5)
    assert_that(result[0].column).is_equal_to(10)
    assert_that(result[0].level).is_equal_to("error")
    assert_that(result[0].code).is_equal_to("invalid_value")
    assert_that(result[0].message).is_equal_to("invalid value")


@pytest.mark.parametrize(
    ("level", "code", "output"),
    [
        (
            "error",
            "invalid_value",
            "error[invalid_value]: missing value\n  --> file.toml:1:5",
        ),
        (
            "warning",
            "deprecated_syntax",
            "warning[deprecated_syntax]: use new syntax\n  --> file.toml:2:1",
        ),
        (
            "error",
            "expected_table_array",
            "error[expected_table_array]: expected table array\n  --> config.toml:10:1",
        ),
    ],
    ids=["error_invalid_value", "warning_deprecated", "error_expected_table_array"],
)
def test_parse_taplo_output_severity_levels(
    level: str,
    code: str,
    output: str,
) -> None:
    """Parse issues with different severity levels.

    Args:
        level: The expected severity level.
        code: The expected error code.
        output: The taplo output to parse.
    """
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].level).is_equal_to(level)
    assert_that(result[0].code).is_equal_to(code)


def test_parse_taplo_output_multiple_issues() -> None:
    """Parse multiple issues from output."""
    output = """error[invalid_value]: first error
  --> file1.toml:1:5
   |
 1 | key =
   |     ^ expected value

error[syntax_error]: second error
  --> file2.toml:10:1
   |
10 | [invalid
   | ^ unclosed bracket

warning[deprecated]: third issue
  --> file3.toml:5:3"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(3)
    assert_that(result[0].line).is_equal_to(1)
    assert_that(result[0].file).is_equal_to("file1.toml")
    assert_that(result[1].line).is_equal_to(10)
    assert_that(result[1].file).is_equal_to("file2.toml")
    assert_that(result[2].line).is_equal_to(5)
    assert_that(result[2].file).is_equal_to("file3.toml")


def test_parse_taplo_output_non_matching_lines_ignored() -> None:
    """Non-matching lines are ignored."""
    output = """Some header text
Taplo version 0.9.0

error[invalid_value]: actual error
  --> pyproject.toml:5:10

Other random output
Done."""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("invalid_value")


def test_parse_taplo_output_file_with_path() -> None:
    """Parse file with directory path."""
    output = """error[invalid_key]: invalid key name
  --> config/settings/app.toml:10:1"""
    result = parse_taplo_output(output)
    assert_that(result[0].file).is_equal_to("config/settings/app.toml")


def test_parse_taplo_output_blank_lines_between_issues() -> None:
    """Handle blank lines between issues."""
    output = """error[error_one]: first
  --> file.toml:1:1


error[error_two]: second
  --> file.toml:5:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(2)


# =============================================================================
# Edge case tests
# =============================================================================


def test_parse_taplo_output_unicode_in_message() -> None:
    """Handle Unicode characters in error messages."""
    output = """error[invalid_value]: caractere invalide
  --> file.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].message).contains("invalide")


def test_parse_taplo_output_very_long_message() -> None:
    """Handle extremely long error messages."""
    long_text = "x" * 5000
    output = f"""error[long_error]: {long_text}
  --> file.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(len(result[0].message)).is_equal_to(5000)


def test_parse_taplo_output_very_large_line_number() -> None:
    """Handle very large line numbers."""
    output = """error[error]: error on large line
  --> file.toml:999999:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(999999)


def test_parse_taplo_output_special_chars_in_message() -> None:
    """Handle special characters in error messages."""
    output = """error[special]: Use "quotes" and <brackets> and [arrays]
  --> file.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].message).contains("quotes")
    assert_that(result[0].message).contains("<brackets>")


def test_parse_taplo_output_colon_in_message() -> None:
    """Handle colons in error messages."""
    output = """error[invalid]: expected format: key = value
  --> file.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].message).contains("key = value")


def test_parse_taplo_output_deeply_nested_path() -> None:
    """Handle deeply nested file paths."""
    deep_path = "a/b/c/d/e/f/g/h/i/j/config.toml"
    output = f"""error[nested]: deep path error
  --> {deep_path}:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to(deep_path)


def test_parse_taplo_output_code_with_underscores() -> None:
    """Handle error codes with underscores."""
    output = """error[expected_table_array_header]: expected table array
  --> file.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("expected_table_array_header")


def test_parse_taplo_output_column_position() -> None:
    """Verify column position is correctly extracted."""
    output = """error[syntax]: error at column
  --> file.toml:5:42"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].column).is_equal_to(42)


def test_parse_taplo_output_windows_path() -> None:
    """Handle Windows-style paths if present."""
    # Note: Taplo typically uses forward slashes even on Windows,
    # but we test path handling nonetheless
    output = """error[error]: windows path
  --> C:/Users/test/config.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    # The path includes drive letter
    assert_that(result[0].file).contains("config.toml")


def test_parse_taplo_output_location_with_extra_spaces() -> None:
    """Handle location line with varying whitespace."""
    output = """error[invalid]: error message
    -->    pyproject.toml:5:10"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("pyproject.toml")


def test_parse_taplo_output_issue_dataclass_fields() -> None:
    """Verify TaploIssue has correct field defaults."""
    output = """error[test_code]: test message
  --> test.toml:1:1"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    issue = result[0]

    # Verify all fields are populated
    assert_that(issue.file).is_equal_to("test.toml")
    assert_that(issue.line).is_equal_to(1)
    assert_that(issue.column).is_equal_to(1)
    assert_that(issue.level).is_equal_to("error")
    assert_that(issue.code).is_equal_to("test_code")
    assert_that(issue.message).is_equal_to("test message")


# =============================================================================
# Format check output tests (taplo fmt --check)
# =============================================================================


def test_parse_taplo_output_fmt_check_format() -> None:
    """Parse taplo fmt --check output format.

    Taplo fmt --check outputs:
    ERROR taplo:format_files: the file is not properly formatted path="file.toml"
    """
    output = (
        "ERROR taplo:format_files: the file is not properly formatted"
        ' path="/tmp/test.toml"'
    )
    result = parse_taplo_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("/tmp/test.toml")
    assert_that(result[0].level).is_equal_to("error")
    assert_that(result[0].code).is_equal_to("format")
    assert_that(result[0].message).is_equal_to("the file is not properly formatted")


def test_parse_taplo_output_fmt_check_multiple_files() -> None:
    """Parse taplo fmt --check output with multiple files."""
    output = (
        "ERROR taplo:format_files:"
        " the file is not properly formatted"
        ' path="config.toml"\n'
        "ERROR taplo:format_files:"
        " the file is not properly formatted"
        ' path="pyproject.toml"\n'
        "ERROR operation failed"
        " error=some files were not properly formatted"
    )
    result = parse_taplo_output(output)

    assert_that(result).is_length(2)
    assert_that(result[0].file).is_equal_to("config.toml")
    assert_that(result[1].file).is_equal_to("pyproject.toml")


def test_parse_taplo_output_fmt_check_with_info_lines() -> None:
    """Parse taplo fmt --check output with INFO lines mixed in."""
    output = """ INFO taplo:format_files:collect_files: found files total=1 excluded=0
ERROR taplo:format_files: the file is not properly formatted path="test.toml"
ERROR operation failed error=some files were not properly formatted"""
    result = parse_taplo_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("test.toml")


def test_parse_taplo_output_fmt_check_rust_log_error_format() -> None:
    """Parse taplo fmt --check output when RUST_LOG=error is set.

    When RUST_LOG=error is set, taplo outputs a simplified format without
    the taplo:format_files: prefix.
    """
    output = """ERROR the file is not properly formatted path="/tmp/test.toml"
ERROR operation failed error=some files were not properly formatted"""
    result = parse_taplo_output(output)

    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("/tmp/test.toml")
    assert_that(result[0].level).is_equal_to("error")
    assert_that(result[0].code).is_equal_to("format")
    assert_that(result[0].message).is_equal_to("the file is not properly formatted")


def test_parse_taplo_output_ansi_codes_stripped() -> None:
    """Strip ANSI escape codes from output for consistent CI/local parsing."""
    # Output with ANSI color codes (common in CI environments)
    output = """\x1b[31merror[invalid_value]: invalid value\x1b[0m
  --> pyproject.toml:5:10"""
    result = parse_taplo_output(output)
    assert_that(result).is_length(1)
    assert_that(result[0].file).is_equal_to("pyproject.toml")
    assert_that(result[0].code).is_equal_to("invalid_value")
