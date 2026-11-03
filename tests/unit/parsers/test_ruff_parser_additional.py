"""Additional tests for Ruff parser coverage gaps."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.ruff.ruff_parser import (
    parse_ruff_output,
    parse_ruff_output_with_stats,
    stream_ruff_output,
)

_DEPRECATION_WARNING = (
    "warning: The top-level linter settings are deprecated in favour of their "
    "counterparts in the `lint` section. Please update the following options "
    "in `project[cache]/pyproject.toml`:\n"
    "  - 'ignore' -> 'lint.ignore'\n"
    "  - 'select' -> 'lint.select'\n"
)
_REMOVED_RULE_WARNING = (
    "warning: The following rules have been removed and ignoring them has no effect:\n"
    "    - ANN101\n"
    "    - UP027\n\n"
)


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "\nwarning: cache [cache]/ruff is unavailable",
        _DEPRECATION_WARNING,
        _REMOVED_RULE_WARNING,
    ],
)
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
def test_ruff_rejects_unusable_required_fields(
    item: dict[str, object],
    suffix: str,
) -> None:
    """Batch, JSON Lines and streaming paths reject incomplete records."""
    for output in (json.dumps([item]), json.dumps(item) + "\n"):
        assert_that(parse_ruff_output(output + suffix)).is_empty()
        assert_that(parse_ruff_output_with_stats(output + suffix)).is_equal_to(([], 1))
        assert_that(list(stream_ruff_output(output + suffix))).is_empty()


@pytest.mark.parametrize(
    "malformed_line",
    ['{"filename":', '{"filename": "broken.py", "message": }'],
)
def test_ruff_json_lines_counts_decode_and_field_failures(malformed_line: str) -> None:
    """Keep valid findings while counting each decoding and required-field failure."""
    output = "\n".join(
        (
            "Ruff progress...",
            "",
            malformed_line,
            '{"filename": "a.py", "message": "unused import", "code": "F401"}',
            "{}",
            '{"file": "b.py", "message": "Expected expression", "code": null}',
            f"  {malformed_line}",
        ),
    )

    issues, failures = parse_ruff_output_with_stats(output)

    assert_that(failures).is_equal_to(3)
    assert_that(issues).extracting("file", "message", "code").is_equal_to(
        [("a.py", "unused import", "F401"), ("b.py", "Expected expression", "")],
    )
    assert_that(parse_ruff_output(output)).is_equal_to(issues)


@pytest.mark.parametrize(
    ("output", "expected_failures"),
    [
        ('{"filename":', 1),
        ('{"filename":\n{"message":', 2),
        (' \n  {"filename":\n\n', 1),
        ("[", 1),
        ("[null,", 1),
        (" \n[null,\n", 1),
        ('[{"filename": "a.py", "message": "broken"}', 1),
        ('[\n{"filename":', 1),
        ("null", 1),
        (" \nnull\t", 1),
        ("0", 1),
        ("42", 1),
        ("-3.5", 1),
        ('""', 1),
        ('"diagnostics"', 1),
        ("true", 1),
        ("false", 1),
        ("", 0),
        (" \n\t", 0),
        ("[]", 0),
        (" \n[]\n", 0),
        ("[\n]", 0),
        ("[]\n[null]", 1),
        ("[]\n[", 1),
        ("[]\n]", 1),
        ("garbage\n[]", 1),
        ("[]\ngarbage", 1),
        ("[]\nnull", 1),
        ("[]\n42", 1),
        ('[]\n"diagnostics"', 1),
        ("[]\nwarning: cache [cache]\n[null]", 1),
        ("[]\nRuff failed", 1),
        ("[]\n  - 'select' -> 'lint.select'", 1),
        ("[]\n    - ANN101", 1),
        ("[]\nwarning: deprecated\n  garbage", 1),
        ("[]\nwarning: deprecated\n  null", 1),
        ("[]\nwarning: deprecated\n  []", 1),
        ("[]\nwarning: deprecated\n  {}", 1),
    ],
)
def test_ruff_rejected_only_and_empty_reports(
    output: str,
    expected_failures: int,
) -> None:
    """Distinguish rejected JSON records from valid empty reports."""
    assert_that(parse_ruff_output_with_stats(output)).is_equal_to(
        ([], expected_failures),
    )
    assert_that(parse_ruff_output(output)).is_empty()


@pytest.mark.parametrize(
    "warning",
    [
        "warning: stray output\n",
        "warning: cache path [cache]/ruff is unavailable\n",
        "warning: cache path cache] is unavailable\n",
        _DEPRECATION_WARNING,
        _REMOVED_RULE_WARNING,
        _DEPRECATION_WARNING + _REMOVED_RULE_WARNING,
    ],
)
@pytest.mark.parametrize(
    ("report", "expected_codes", "expected_failures"),
    [
        ("null", [], 1),
        ("0", [], 1),
        ("42", [], 1),
        ("-3.5", [], 1),
        ('""', [], 1),
        ('"diagnostics"', [], 1),
        ("true", [], 1),
        ("false", [], 1),
        ("[]", [], 0),
        (" \n[]\n", [], 0),
        ("[\n]", [], 0),
        ("array", [""], 0),
        ("object", [""], 0),
        ("json-lines", ["", "F401"], 0),
    ],
)
def test_ruff_warning_framing_preserves_parser_statistics(
    report: str,
    expected_codes: list[str],
    expected_failures: int,
    warning: str,
) -> None:
    """Warnings preserve arrays and JSON Lines without accepting scalars."""
    diagnostic = {"file": "syntax.py", "message": "Expected expression", "code": None}
    reports = {
        "array": json.dumps([diagnostic], indent=2),
        "object": json.dumps(diagnostic),
        "json-lines": json.dumps(diagnostic)
        + '\n{"filename": "a.py", "message": "unused import", "code": "F401"}',
    }
    output = reports.get(report, report) + "\n" + warning

    issues, failures = parse_ruff_output_with_stats(output)

    assert_that(failures).is_equal_to(expected_failures)
    assert_that(issues).extracting("code").is_equal_to(expected_codes)
    assert_that(issues).extracting("file", "message").is_equal_to(
        [("syntax.py", "Expected expression"), ("a.py", "unused import")][
            : len(expected_codes)
        ],
    )
    assert_that(parse_ruff_output(output)).is_equal_to(issues)


@pytest.mark.parametrize(
    "suffix",
    [
        "error: Failed to read [cache]/other.py (os error 2)",
        "error: Ruff failed\n  Cause: configuration error",
        "Ruff failed",
        "[]",
        "[",
        "]",
        '{"filename": "extra.py", "message": "extra record"}',
        "null",
        "42",
        '"diagnostics"',
        "true",
        "garbage",
        _DEPRECATION_WARNING + "  garbage",
        _DEPRECATION_WARNING + "  []",
        _DEPRECATION_WARNING + "  null",
        _REMOVED_RULE_WARNING + "    garbage",
        _REMOVED_RULE_WARNING + "    - []",
        _REMOVED_RULE_WARNING + "    - null",
        _REMOVED_RULE_WARNING + "    - ANN101 trailing garbage",
        _REMOVED_RULE_WARNING + '{"filename": "extra.py", "message": "extra"}',
    ],
)
def test_ruff_invalid_suffix_keeps_array_findings_and_counts_failures(
    suffix: str,
) -> None:
    """Retain decoded findings and field failures while rejecting trailing data."""
    report = json.dumps(
        [{"file": "syntax.py", "message": "Expected expression", "code": None}, {}],
        indent=2,
    )

    issues, failures = parse_ruff_output_with_stats(report + "\n" + suffix)

    assert_that(issues).extracting("file", "message", "code").is_equal_to(
        [("syntax.py", "Expected expression", "")],
    )
    assert_that(failures).is_equal_to(2)


@pytest.mark.parametrize("prefix", ["", "Ruff progress...\n"])
def test_ruff_valid_json_lines_do_not_count_array_decode_failure(prefix: str) -> None:
    """Successful JSON Lines fallback does not inherit the array decoder failure."""
    output = (
        prefix + '{"filename": "a.py", "message": "unused import", "code": "F401"}\n'
        '{"file": "b.py", "message": "Expected expression", "code": null}\n'
    )

    issues, failures = parse_ruff_output_with_stats(output)

    assert_that(failures).is_zero()
    assert_that(issues).extracting("file", "message", "code").is_equal_to(
        [("a.py", "unused import", "F401"), ("b.py", "Expected expression", "")],
    )


@pytest.mark.parametrize("prefix", ["", " \n\t"])
@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "\nwarning: cache [cache]/ruff is unavailable",
        _DEPRECATION_WARNING,
        _REMOVED_RULE_WARNING,
    ],
)
def test_ruff_truncated_array_keeps_diagnostics_and_counts_failure(
    prefix: str,
    suffix: str,
) -> None:
    """Recover complete diagnostics without hiding an invalid array envelope."""
    output = prefix + '[\n{"filename":"a.py","message":"bad"}\n' + suffix

    issues, failures = parse_ruff_output_with_stats(output)

    assert_that(issues).extracting("file", "message").is_equal_to([("a.py", "bad")])
    assert_that(failures).is_equal_to(1)
    assert_that(parse_ruff_output(output)).is_equal_to(issues)


def test_ruff_null_code_keeps_syntax_error_and_file_alias() -> None:
    """Syntax errors remain findings when Ruff supplies no rule code."""
    output = '[{"file": "syntax.py", "message": "Expected expression", "code": null}]'
    issues = parse_ruff_output(output)

    assert_that(issues).is_length(1)
    assert_that((issues[0].file, issues[0].message, issues[0].code)).is_equal_to(
        ("syntax.py", "Expected expression", ""),
    )
    assert_that(parse_ruff_output_with_stats(output)).is_equal_to((issues, 0))
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
