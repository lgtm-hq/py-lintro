"""Additional tests for Ruff parser coverage gaps."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.ruff.ruff_parser import parse_ruff_output, stream_ruff_output


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"filename": "a.py"},
        {"message": "bad syntax"},
        {"filename": None, "message": "bad syntax"},
        {"filename": "a.py", "message": None},
        {"filename": 42, "message": "bad syntax"},
        {"filename": "a.py", "message": []},
        {"filename": "  ", "message": "bad syntax"},
        {"filename": "a.py", "message": " "},
    ],
)
def test_ruff_rejects_unusable_required_fields(item: dict[str, object]) -> None:
    """Batch, JSON Lines and streaming paths reject incomplete records."""
    for output in (json.dumps([item]), json.dumps(item) + "\n"):
        assert_that(parse_ruff_output(output)).is_empty()
        assert_that(list(stream_ruff_output(output))).is_empty()


def test_ruff_null_code_keeps_syntax_error_and_file_alias() -> None:
    """Syntax errors remain findings when Ruff supplies no rule code."""
    output = '[{"file": "syntax.py", "message": "Expected expression", "code": null}]'
    issues = parse_ruff_output(output)

    assert_that(issues).is_length(1)
    assert_that((issues[0].file, issues[0].message, issues[0].code)).is_equal_to(
        ("syntax.py", "Expected expression", ""),
    )
    assert_that(list(stream_ruff_output(output))).is_equal_to(issues)


def test_parse_ruff_output_plain_json_array() -> None:
    """Parse a simple JSON array output into issues list."""
    output = (
        "[\n"
        '  {"filename": "a.py", "location": {"row": 1, "column": 2},'
        '   "code": "E1", "message": "x"}\n'
        "]"
    )
    issues = parse_ruff_output(output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].file.endswith("a.py")).is_true()


def test_parse_ruff_output_empty_and_malformed_line_skipped() -> None:
    """Skip empty and malformed lines in JSONL output gracefully."""
    jl = (
        "\n\n"  # empties ignored
        '{"filename":"b.py","location":{"row":2,"column":1},"code":"F","message":"m"}\n'
        "not-json\n"  # malformed line skipped
    )
    issues = parse_ruff_output(jl)
    files = [i.file for i in issues]
    assert_that(files).contains("b.py")


# =============================================================================
# Edge case tests
# =============================================================================


def test_parse_ruff_output_unicode_file_path() -> None:
    """Handle Unicode characters in file paths."""
    output = (
        '{"filename": "src/código/módulo.py", "location": {"row": 1, "column": 1}, '
        '"code": "E501", "message": "line too long"}'
    )
    issues = parse_ruff_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).contains("módulo.py")


def test_parse_ruff_output_file_path_with_spaces() -> None:
    """Handle file paths with spaces."""
    output = (
        '{"filename": "my project/source files/main.py", '
        '"location": {"row": 5, "column": 10}, "code": "F401", "message": "unused"}'
    )
    issues = parse_ruff_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).contains("my project")


def test_parse_ruff_output_special_chars_in_message() -> None:
    """Handle special characters in error messages."""
    output = (
        '{"filename": "test.py", "location": {"row": 1, "column": 1}, '
        '"code": "E501", "message": "Line contains \\"quotes\\" and <brackets>"}'
    )
    issues = parse_ruff_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].message).contains("quotes")


def test_parse_ruff_output_null_values_handled() -> None:
    """Handle null values in JSON output gracefully."""
    output = (
        '{"filename": "test.py", "location": {"row": 1, "column": null}, '
        '"code": "E501", "message": "error"}'
    )
    issues = parse_ruff_output(output)
    # Should either parse with default column or handle gracefully
    assert_that(issues).is_length(1)


def test_parse_ruff_output_very_long_file_path() -> None:
    """Handle very long file paths."""
    long_path = "src/" + "nested/" * 50 + "deep_file.py"
    output = (
        f'{{"filename": "{long_path}", "location": {{"row": 1, "column": 1}}, '
        '"code": "E501", "message": "error"}'
    )
    issues = parse_ruff_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).contains("deep_file.py")


def test_parse_ruff_output_extremely_long_message() -> None:
    """Handle extremely long error messages."""
    long_message = "x" * 10000
    output = (
        f'{{"filename": "test.py", "location": {{"row": 1, "column": 1}}, '
        f'"code": "E501", "message": "{long_message}"}}'
    )
    issues = parse_ruff_output(output)
    assert_that(issues).is_length(1)
    assert_that(len(issues[0].message)).is_equal_to(10000)


def test_parse_ruff_output_zero_line_number() -> None:
    """Handle zero line number (edge case)."""
    output = (
        '{"filename": "test.py", "location": {"row": 0, "column": 1}, '
        '"code": "E501", "message": "error"}'
    )
    issues = parse_ruff_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)


def test_parse_ruff_output_negative_column() -> None:
    """Handle negative column number gracefully."""
    output = (
        '{"filename": "test.py", "location": {"row": 1, "column": -1}, '
        '"code": "E501", "message": "error"}'
    )
    issues = parse_ruff_output(output)
    # Should handle gracefully without crashing
    assert_that(issues).is_length(1)
