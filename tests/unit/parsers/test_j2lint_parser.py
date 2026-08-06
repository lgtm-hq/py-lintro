"""Unit tests for the j2lint JSON output parser.

These tests validate that the parser handles empty/invalid input, maps the
``ERRORS`` and ``WARNINGS`` buckets to the correct severity levels, and
tolerates malformed entries.
"""

import json

from assertpy import assert_that

from lintro.parsers.j2lint.j2lint_parser import parse_j2lint_output

_SAMPLE = {
    "ERRORS": [
        {
            "id": "S3",
            "message": "Bad Indentation, expected 4, got 1",
            "filename": "template.j2",
            "line_number": 3,
            "line": "{%- for item in items %}",
            "severity": "HIGH",
        },
        {
            "id": "S1",
            "message": "A single space should be added",
            "filename": "template.j2",
            "line_number": 4,
            "line": "{{item}}",
            "severity": "LOW",
        },
    ],
    "WARNINGS": [
        {
            "id": "S6",
            "message": "Jinja statements should not have delimiters",
            "filename": "template.j2",
            "line_number": 3,
            "line": "{%- for item in items %}",
            "severity": "LOW",
        },
    ],
}


def test_parse_none_returns_empty() -> None:
    """Return an empty list when the parser input is None."""
    assert_that(parse_j2lint_output(None)).is_equal_to([])


def test_parse_empty_string_returns_empty() -> None:
    """Return an empty list for empty parser input."""
    assert_that(parse_j2lint_output("")).is_equal_to([])


def test_parse_invalid_json_returns_empty() -> None:
    """Return an empty list when the output is not valid JSON."""
    assert_that(parse_j2lint_output("not json at all")).is_equal_to([])


def test_parse_clean_report_returns_empty() -> None:
    """Return an empty list when both buckets are empty."""
    output = json.dumps({"ERRORS": [], "WARNINGS": []})
    assert_that(parse_j2lint_output(output)).is_equal_to([])


def test_parse_errors_and_warnings() -> None:
    """Parse both buckets and assign the correct level to each entry."""
    issues = parse_j2lint_output(json.dumps(_SAMPLE))
    assert_that(issues).is_length(3)

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    assert_that(errors).is_length(2)
    assert_that(warnings).is_length(1)

    first = errors[0]
    assert_that(first.file).is_equal_to("template.j2")
    assert_that(first.line).is_equal_to(3)
    assert_that(first.code).is_equal_to("S3")
    assert_that(first.native_severity).is_equal_to("HIGH")
    assert_that(first.message).contains("Bad Indentation")


def test_parse_extracts_json_from_surrounding_text() -> None:
    """Locate the JSON object even when wrapped by extra log lines."""
    output = "some log line\n" + json.dumps(_SAMPLE) + "\ntrailing noise"
    issues = parse_j2lint_output(output)
    assert_that(issues).is_length(3)


def test_parse_skips_entries_without_filename() -> None:
    """Skip malformed entries that lack a filename."""
    payload = {
        "ERRORS": [
            {"id": "S3", "message": "no file", "line_number": 1},
            {
                "id": "S1",
                "message": "ok",
                "filename": "a.j2",
                "line_number": 2,
            },
        ],
        "WARNINGS": [],
    }
    issues = parse_j2lint_output(json.dumps(payload))
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("a.j2")


def test_parse_defaults_missing_line_number_to_zero() -> None:
    """Default the line to zero when line_number is missing or non-integer."""
    payload = {
        "ERRORS": [
            {
                "id": "S0",
                "message": "syntax",
                "filename": "a.j2",
                "line_number": "bad",
            },
        ],
        "WARNINGS": [],
    }
    issues = parse_j2lint_output(json.dumps(payload))
    assert_that(issues).is_length(1)
    assert_that(issues[0].line).is_equal_to(0)


def test_display_row_maps_level_to_severity() -> None:
    """The display row should surface the bucket-derived severity."""
    issues = parse_j2lint_output(json.dumps(_SAMPLE))
    row = issues[0].to_display_row()
    assert_that(row["code"]).is_equal_to("S3")
    assert_that(row["severity"]).is_equal_to("ERROR")
