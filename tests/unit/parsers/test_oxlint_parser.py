"""Unit tests for Oxlint parser functionality."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.oxlint.oxlint_parser import parse_oxlint_output


def test_parse_oxlint_output_empty() -> None:
    """Test parsing empty Oxlint output."""
    issues = parse_oxlint_output("")
    assert_that(issues).is_empty()


def test_parse_oxlint_output_none_string() -> None:
    """Test parsing None-like empty Oxlint output."""
    issues = parse_oxlint_output("")
    assert_that(issues).is_empty()

    issues = parse_oxlint_output("   ")
    assert_that(issues).is_empty()


def test_parse_oxlint_output_malformed_json() -> None:
    """Test parsing malformed JSON Oxlint output."""
    issues = parse_oxlint_output("{invalid json")
    assert_that(issues).is_empty()


def test_parse_oxlint_output_not_dict() -> None:
    """Test parsing Oxlint output that is not a dictionary."""
    issues = parse_oxlint_output("[]")
    assert_that(issues).is_empty()


def test_parse_oxlint_output_no_json_braces() -> None:
    """Test parsing output without JSON object."""
    issues = parse_oxlint_output("some non-json text without braces")
    assert_that(issues).is_empty()


def test_parse_oxlint_output_single_diagnostic() -> None:
    """Test parsing Oxlint output with a single diagnostic."""
    json_output = """{
        "diagnostics": [{
            "message": "Variable 'unused' is declared but never used.",
            "code": "eslint(no-unused-vars)",
            "severity": "warning",
            "filename": "src/test.js",
            "labels": [{"span": {"line": 4, "column": 5}}],
            "help": "Consider removing or using the variable."
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(1)

    issue = issues[0]
    assert_that(issue.file).is_equal_to("src/test.js")
    assert_that(issue.line).is_equal_to(4)
    assert_that(issue.column).is_equal_to(5)
    assert_that(issue.message).is_equal_to(
        "Variable 'unused' is declared but never used.",
    )
    assert_that(issue.code).is_equal_to("eslint(no-unused-vars)")
    assert_that(issue.severity).is_equal_to("warning")
    assert_that(issue.fixable).is_false()
    assert_that(issue.help).is_equal_to("Consider removing or using the variable.")


def test_parse_oxlint_output_multiple_diagnostics() -> None:
    """Test parsing Oxlint output with multiple diagnostics."""
    json_output = """{
        "diagnostics": [
            {
                "message": "Variable 'unused' is declared but never used.",
                "code": "eslint(no-unused-vars)",
                "severity": "warning",
                "filename": "src/test.js",
                "labels": [{"span": {"line": 4, "column": 5}}]
            },
            {
                "message": "Using == may be unsafe.",
                "code": "eslint(eqeqeq)",
                "severity": "error",
                "filename": "src/test.js",
                "labels": [{"span": {"line": 10, "column": 12}}]
            }
        ]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(2)

    # First issue (warning)
    assert_that(issues[0].severity).is_equal_to("warning")
    assert_that(issues[0].code).is_equal_to("eslint(no-unused-vars)")
    assert_that(issues[0].line).is_equal_to(4)

    # Second issue (error)
    assert_that(issues[1].severity).is_equal_to("error")
    assert_that(issues[1].code).is_equal_to("eslint(eqeqeq)")
    assert_that(issues[1].line).is_equal_to(10)


def test_parse_oxlint_output_missing_filename() -> None:
    """Test parsing Oxlint output with missing filename."""
    json_output = """{
        "diagnostics": [{
            "message": "Test error",
            "code": "test-rule",
            "severity": "error",
            "labels": [{"span": {"line": 1, "column": 1}}]
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_empty()


def test_parse_oxlint_output_missing_labels() -> None:
    """Missing labels leave the location unknown."""
    json_output = """{
        "diagnostics": [{
            "message": "Test error",
            "code": "test-rule",
            "severity": "error",
            "filename": "test.js"
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.line).is_zero()
    assert_that(issue.column).is_zero()


def test_parse_oxlint_output_empty_labels() -> None:
    """Empty labels leave the location unknown."""
    json_output = """{
        "diagnostics": [{
            "message": "Test error",
            "code": "test-rule",
            "severity": "error",
            "filename": "test.js",
            "labels": []
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.line).is_zero()
    assert_that(issue.column).is_zero()


def test_parse_oxlint_output_missing_span() -> None:
    """A label without a span leaves the location unknown."""
    json_output = """{
        "diagnostics": [{
            "message": "Test error",
            "code": "test-rule",
            "severity": "error",
            "filename": "test.js",
            "labels": [{}]
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.line).is_zero()
    assert_that(issue.column).is_zero()


def test_parse_oxlint_output_with_extra_text() -> None:
    """Test parsing Oxlint output with extra text after JSON."""
    json_content = (
        '{"diagnostics": [{"message": "Test error", "code": "test-rule", '
        '"severity": "error", "filename": "test.js", '
        '"labels": [{"span": {"line": 1, "column": 1}}]}]}'
    )
    extra_text = "\nSome extra output from oxlint...\n"

    issues = parse_oxlint_output(json_content + extra_text)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("test.js")


def test_parse_oxlint_output_with_prefix_text() -> None:
    """Test parsing Oxlint output with prefix text before JSON."""
    json_content = (
        '{"diagnostics": [{"message": "Test error", "code": "test-rule", '
        '"severity": "error", "filename": "test.js", '
        '"labels": [{"span": {"line": 1, "column": 1}}]}]}'
    )
    prefix_text = "Running oxlint...\n"

    issues = parse_oxlint_output(prefix_text + json_content)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_equal_to("test.js")


def test_parse_oxlint_output_missing_optional_fields() -> None:
    """Test parsing Oxlint output with missing optional fields."""
    json_output = """{
        "diagnostics": [{
            "filename": "test.js",
            "message": "Test error",
            "labels": [{"span": {"line": 1, "column": 1}}]
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(1)

    issue = issues[0]
    assert_that(issue.file).is_equal_to("test.js")
    assert_that(issue.message).is_equal_to("Test error")
    assert_that(issue.code).is_equal_to("")
    assert_that(issue.severity).is_equal_to("warning")  # Default
    assert_that(issue.help).is_none()


def test_parse_oxlint_output_diagnostics_not_list() -> None:
    """Test parsing Oxlint output where diagnostics is not a list."""
    json_output = '{"diagnostics": "not a list"}'

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_empty()


def test_parse_oxlint_output_diagnostic_not_dict() -> None:
    """Test parsing Oxlint output where diagnostic is not a dict."""
    json_output = '{"diagnostics": ["not a dict"]}'

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_empty()


def test_parse_oxlint_output_empty_filename() -> None:
    """Test parsing Oxlint output with empty filename."""
    json_output = """{
        "diagnostics": [{
            "message": "Test error",
            "code": "test-rule",
            "severity": "error",
            "filename": "",
            "labels": [{"span": {"line": 1, "column": 1}}]
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_empty()


def test_parse_oxlint_output_no_help_field() -> None:
    """Test parsing Oxlint output without help field."""
    json_output = """{
        "diagnostics": [{
            "message": "Test error",
            "code": "test-rule",
            "severity": "error",
            "filename": "test.js",
            "labels": [{"span": {"line": 1, "column": 1}}]
        }]
    }"""

    issues = parse_oxlint_output(json_output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].help).is_none()


@pytest.mark.parametrize("field", ["filename", "message"])
@pytest.mark.parametrize("value", [None, 42, False, [], {}])
def test_required_fields_reject_unrenderable_values(field: str, value: object) -> None:
    """Invalid required fields never reach the issue model."""
    diagnostic = {"filename": "test.js", "message": "Unused variable", field: value}

    assert_that(
        parse_oxlint_output(json.dumps({"diagnostics": [diagnostic]})),
    ).is_empty()


@pytest.mark.parametrize("field", ["filename", "message"])
def test_missing_required_field_is_not_a_diagnostic(field: str) -> None:
    """An absent required string cannot become a defaulted issue."""
    diagnostic = {"filename": "test.js", "message": "Unused variable"}
    del diagnostic[field]

    issues = parse_oxlint_output(json.dumps({"diagnostics": [diagnostic]}))

    assert_that(issues).is_empty()


def test_null_optional_fields_use_model_defaults() -> None:
    """Explicit nulls normalize before issue rendering or serialization."""
    diagnostic = {
        "filename": "test.js",
        "message": "Unused variable",
        "code": None,
        "severity": None,
        "labels": [{"span": {"line": None, "column": None}}],
    }

    issues = parse_oxlint_output(json.dumps({"diagnostics": [diagnostic]}))

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that((issue.code, issue.severity, issue.line, issue.column)).is_equal_to(
        ("", "warning", 0, 0),
    )
    row = issue.to_display_row()
    assert_that((row["line"], row["column"], row["message"])).is_equal_to(
        ("-", "-", "Unused variable"),
    )


@pytest.mark.parametrize("labels", [None, [None], [{"span": None}], [{"span": {}}]])
def test_missing_location_is_unknown(labels: object) -> None:
    """Nullable location containers preserve a finding without inventing 1:1."""
    diagnostic = {"filename": "test.js", "message": "Unused variable", "labels": labels}

    issues = parse_oxlint_output(json.dumps({"diagnostics": [diagnostic]}))

    assert_that(issues).is_length(1)
    assert_that((issues[0].line, issues[0].column)).is_equal_to((0, 0))


@pytest.mark.parametrize("line,column", [(0, 0), (3, None), (None, 7)])
def test_coordinates_default_independently(
    line: int | None,
    column: int | None,
) -> None:
    """Keep each known coordinate when its counterpart is absent."""
    diagnostic = {
        "filename": "test.js",
        "message": "Unused variable",
        "labels": [{"span": {"line": line, "column": column}}],
    }

    issue = parse_oxlint_output(json.dumps({"diagnostics": [diagnostic]}))[0]

    assert_that((issue.line, issue.column)).is_equal_to(
        (0 if line is None else line, 0 if column is None else column),
    )
