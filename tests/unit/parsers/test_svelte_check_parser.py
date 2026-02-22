"""Unit tests for svelte-check parser."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.parsers.svelte_check.svelte_check_issue import SvelteCheckIssue
from lintro.parsers.svelte_check.svelte_check_parser import parse_svelte_check_output


def test_parse_svelte_check_output_empty() -> None:
    """Handle empty output."""
    assert_that(parse_svelte_check_output("")).is_empty()
    assert_that(parse_svelte_check_output("   \n\n  ")).is_empty()


# --- NDJSON format tests (modern svelte-check --output machine-verbose) ---


def test_parse_ndjson_with_timestamp_prefix() -> None:
    """Parse NDJSON line with leading millisecond timestamp prefix."""
    payload = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type 'string' is not assignable to type 'number'.",
        },
    )
    output = f"1590680326283 {payload}"
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")
    assert_that(issues[0].line).is_equal_to(15)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[0].message).contains("not assignable")


def test_parse_ndjson_single_error() -> None:
    """Parse a single NDJSON error line."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type 'string' is not assignable to type 'number'.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0]).is_instance_of(SvelteCheckIssue)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")
    assert_that(issues[0].line).is_equal_to(15)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[0].message).contains("not assignable")


def test_parse_ndjson_warning() -> None:
    """Parse an NDJSON warning line."""
    output = json.dumps(
        {
            "type": "WARNING",
            "fn": "src/lib/Card.svelte",
            "start": {"line": 8, "character": 1},
            "end": {"line": 8, "character": 20},
            "message": "Unused CSS selector '.unused'.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("warning")


def test_parse_ndjson_multiple_lines() -> None:
    """Parse multiple NDJSON lines."""
    lines = [
        json.dumps(
            {
                "type": "ERROR",
                "fn": "src/lib/Button.svelte",
                "start": {"line": 15, "character": 5},
                "end": {"line": 15, "character": 10},
                "message": "Type error.",
            },
        ),
        json.dumps(
            {
                "type": "WARNING",
                "fn": "src/lib/Card.svelte",
                "start": {"line": 8, "character": 1},
                "end": {"line": 8, "character": 20},
                "message": "Unused CSS selector.",
            },
        ),
    ]
    output = "\n".join(lines)
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(2)
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[1].severity).is_equal_to("warning")


def test_parse_ndjson_filename_field() -> None:
    """Parse NDJSON using 'filename' field instead of 'fn'."""
    output = json.dumps(
        {
            "type": "ERROR",
            "filename": "src/lib/Button.svelte",
            "start": {"line": 10, "character": 3},
            "end": {"line": 10, "character": 8},
            "message": "Type mismatch.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")


def test_parse_ndjson_multiline_span() -> None:
    """Parse NDJSON issue spanning multiple lines."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 18, "character": 10},
            "message": "Multi-line type error.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].end_line).is_equal_to(18)
    assert_that(issues[0].end_column).is_equal_to(10)


def test_parse_ndjson_same_position() -> None:
    """NDJSON same start/end position sets end_line/end_column to None."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 5},
            "message": "Point error.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].end_line).is_none()
    assert_that(issues[0].end_column).is_none()


def test_parse_ndjson_windows_paths() -> None:
    """NDJSON backslash paths are normalized to forward slashes."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src\\lib\\Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type mismatch.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")


def test_parse_ndjson_code_field() -> None:
    """Parse NDJSON with a code field."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type 'string' is not assignable to type 'number'.",
            "code": "ts-2322",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("ts-2322")


def test_parse_ndjson_no_code_field() -> None:
    """NDJSON without code field leaves code as empty string."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type error.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("")


def test_parse_ndjson_numeric_code_field() -> None:
    """NDJSON with numeric code is coerced to string."""
    output = json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type error.",
            "code": 2322,
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("2322")


def test_parse_ndjson_invalid_json_skipped() -> None:
    """Non-JSON lines are skipped by NDJSON parser."""
    output = "not valid json\n" + json.dumps(
        {
            "type": "ERROR",
            "fn": "src/lib/Button.svelte",
            "start": {"line": 15, "character": 5},
            "end": {"line": 15, "character": 10},
            "message": "Type error.",
        },
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)


# --- Legacy plain-text machine-verbose format tests ---


def test_parse_svelte_check_output_machine_verbose_single_error() -> None:
    """Parse a single legacy machine-verbose error."""
    output = (
        "src/lib/Button.svelte:15:5:15:10 Error "
        "Type 'string' is not assignable to type 'number'."
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")
    assert_that(issues[0].line).is_equal_to(15)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[0].message).contains("not assignable")


def test_parse_svelte_check_output_multiple_errors() -> None:
    """Parse multiple errors from svelte-check output."""
    output = (
        "src/lib/Button.svelte:15:5:15:10 Error"
        " Type 'string' is not assignable to type 'number'.\n"
        "src/routes/+page.svelte:20:3:20:15 Error"
        " Property 'foo' does not exist on type 'Bar'.\n"
        "src/lib/Card.svelte:8:1:8:20 Warning"
        " Unused CSS selector '.unused'."
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(3)
    assert_that(issues[0].severity).is_equal_to("error")
    assert_that(issues[1].severity).is_equal_to("error")
    assert_that(issues[2].severity).is_equal_to("warning")


def test_parse_svelte_check_output_machine_format() -> None:
    """Parse machine format (non-verbose)."""
    output = (
        "ERROR src/lib/Button.svelte:15:5"
        " Type 'string' is not assignable to type 'number'."
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")
    assert_that(issues[0].line).is_equal_to(15)
    assert_that(issues[0].column).is_equal_to(5)
    assert_that(issues[0].severity).is_equal_to("error")


def test_parse_svelte_check_output_warning_severity() -> None:
    """Parse warning severity level."""
    output = "src/lib/Card.svelte:8:1:8:20 Warning Unused CSS selector '.unused'."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("warning")


def test_parse_svelte_check_output_hint_severity() -> None:
    """Parse hint severity level."""
    output = (
        "src/lib/Card.svelte:8:1:8:20 Hint Consider using a more specific selector."
    )
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("hint")


def test_parse_svelte_check_output_windows_paths() -> None:
    """Normalize Windows backslashes to forward slashes."""
    output = r"src\lib\Button.svelte:15:5:15:10 Error Type mismatch."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")


def test_parse_svelte_check_output_ansi_codes() -> None:
    """Strip ANSI escape codes from output."""
    output = "\x1b[31msrc/lib/Button.svelte:15:5:15:10 Error Type mismatch.\x1b[0m"
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")


def test_parse_svelte_check_output_skips_noise_lines() -> None:
    """Skip non-error lines like summary and progress."""
    output = """====================================
Loading svelte-check in workspace...
Diagnostics:
src/lib/Button.svelte:15:5:15:10 Error Type mismatch.
svelte-check found 1 error"""
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("src/lib/Button.svelte")


def test_parse_svelte_check_output_end_line_different() -> None:
    """Parse issue spanning multiple lines."""
    output = "src/lib/Button.svelte:15:5:18:10 Error Multi-line type error."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].end_line).is_equal_to(18)
    assert_that(issues[0].end_column).is_equal_to(10)


def test_parse_svelte_check_output_same_line_same_column() -> None:
    """End line/column set to None when same as start."""
    output = "src/lib/Button.svelte:15:5:15:5 Error Point error."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].end_line).is_none()
    assert_that(issues[0].end_column).is_none()


def test_parse_svelte_check_output_same_line_different_column() -> None:
    """Same-line span preserves end_column when it differs from start."""
    output = "src/lib/Button.svelte:15:5:15:10 Error Inline span error."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].end_line).is_none()
    assert_that(issues[0].end_column).is_equal_to(10)


def test_parse_svelte_check_output_warn_machine_format() -> None:
    """Parse WARN severity in machine format."""
    output = "WARN src/lib/Card.svelte:8:1 Unused CSS selector."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("warning")


def test_parse_svelte_check_output_hint_machine_format() -> None:
    """Parse HINT severity in machine format."""
    output = "HINT src/lib/Card.svelte:8:1 Consider refactoring."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].severity).is_equal_to("hint")


def test_svelte_check_issue_type() -> None:
    """Verify parsed issues are SvelteCheckIssue instances."""
    output = "src/lib/Button.svelte:15:5:15:10 Error Type error."
    issues = parse_svelte_check_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0]).is_instance_of(SvelteCheckIssue)
