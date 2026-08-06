"""Unit tests for PHPStan output parsing and the PHPStan issue model."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue
from lintro.parsers.phpstan.phpstan_parser import parse_phpstan_output

# Real captured output from `phpstan analyse --level=0 --error-format=json`
# on a file with two seeded errors (PHPStan 2.2.5).
REAL_OUTPUT: str = json.dumps(
    {
        "totals": {"errors": 0, "file_errors": 2},
        "files": {
            "src/App.php": {
                "errors": 2,
                "messages": [
                    {
                        "message": "Function add invoked with 1 parameter, 2 required.",
                        "line": 5,
                        "ignorable": True,
                        "identifier": "arguments.count",
                    },
                    {
                        "message": "Function nonExistentFunction not found.",
                        "line": 6,
                        "ignorable": True,
                        "tip": "Learn more at https://phpstan.org/user-guide/"
                        "discovering-symbols",
                        "identifier": "function.notFound",
                    },
                ],
            },
        },
        "errors": [],
    },
)


def test_parse_real_output_field_extraction() -> None:
    """Parser extracts file, line, message, identifier, and tip from real output."""
    issues = parse_phpstan_output(output=REAL_OUTPUT)
    assert_that(len(issues)).is_equal_to(2)

    first = issues[0]
    assert_that(first.file).is_equal_to("src/App.php")
    assert_that(first.line).is_equal_to(5)
    assert_that(first.identifier).is_equal_to("arguments.count")
    assert_that(first.message).contains("2 required")
    assert_that(first.tip).is_equal_to("")
    assert_that(first.ignorable).is_true()

    second = issues[1]
    assert_that(second.identifier).is_equal_to("function.notFound")
    assert_that(second.line).is_equal_to(6)
    assert_that(second.tip).contains("discovering-symbols")


def test_parse_multiple_files() -> None:
    """Messages across multiple files are all collected."""
    output = json.dumps(
        {
            "totals": {"errors": 0, "file_errors": 2},
            "files": {
                "a.php": {
                    "errors": 1,
                    "messages": [
                        {"message": "A", "line": 1, "identifier": "x.a"},
                    ],
                },
                "b.php": {
                    "errors": 1,
                    "messages": [
                        {"message": "B", "line": 2, "identifier": "x.b"},
                    ],
                },
            },
            "errors": [],
        },
    )
    issues = parse_phpstan_output(output=output)
    assert_that(len(issues)).is_equal_to(2)
    files = {issue.file for issue in issues}
    assert_that(files).is_equal_to({"a.php", "b.php"})


def test_parse_top_level_errors() -> None:
    """Top-level (non-file) errors are surfaced as issues with empty file."""
    output = json.dumps(
        {
            "totals": {"errors": 1, "file_errors": 0},
            "files": {},
            "errors": ["Configuration file is invalid."],
        },
    )
    issues = parse_phpstan_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].file).is_equal_to("")
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].message).is_equal_to("Configuration file is invalid.")
    assert_that(issues[0].ignorable).is_false()


def test_parse_clean_output_returns_empty() -> None:
    """A clean run (no findings) yields no issues."""
    output = json.dumps(
        {"totals": {"errors": 0, "file_errors": 0}, "files": {}, "errors": []},
    )
    assert_that(parse_phpstan_output(output=output)).is_equal_to([])


def test_parse_none_output() -> None:
    """None output returns an empty list."""
    assert_that(parse_phpstan_output(output=None)).is_equal_to([])


def test_parse_empty_string_output() -> None:
    """Empty string output returns an empty list."""
    assert_that(parse_phpstan_output(output="")).is_equal_to([])


def test_parse_invalid_json() -> None:
    """Invalid JSON returns an empty list without raising."""
    assert_that(parse_phpstan_output(output="not json {")).is_equal_to([])


def test_parse_non_object_json() -> None:
    """A non-object JSON root returns an empty list."""
    assert_that(parse_phpstan_output(output=json.dumps([1, 2, 3]))).is_equal_to([])


def test_parse_missing_files_key() -> None:
    """Missing 'files' key behaves as no file findings."""
    assert_that(parse_phpstan_output(output=json.dumps({"errors": []}))).is_equal_to([])


def test_parse_message_missing_line_defaults_zero() -> None:
    """A message without a line number defaults line to 0."""
    output = json.dumps(
        {
            "files": {
                "a.php": {
                    "messages": [
                        {"message": "no line", "line": None, "identifier": "x.y"},
                    ],
                },
            },
            "errors": [],
        },
    )
    issues = parse_phpstan_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].line).is_equal_to(0)


def test_parse_skips_malformed_messages() -> None:
    """Malformed message entries are skipped gracefully."""
    output = json.dumps(
        {
            "files": {
                "a.php": {
                    "messages": [
                        None,
                        42,
                        {"line": 3, "identifier": "x.y"},
                        {"message": "valid", "line": 4, "identifier": "x.z"},
                    ],
                },
            },
            "errors": [],
        },
    )
    issues = parse_phpstan_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].message).is_equal_to("valid")


def test_parse_missing_identifier_defaults_empty() -> None:
    """A message without an identifier yields an empty code."""
    output = json.dumps(
        {
            "files": {
                "a.php": {
                    "messages": [{"message": "m", "line": 1}],
                },
            },
            "errors": [],
        },
    )
    issues = parse_phpstan_output(output=output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].identifier).is_equal_to("")


def test_issue_display_row_maps_identifier_to_code() -> None:
    """The issue maps identifier -> code and level -> severity in display rows."""
    issue = PhpstanIssue(
        file="a.php",
        line=10,
        column=0,
        message="Boom",
        identifier="function.notFound",
        level="error",
        tip="fix it",
    )
    row = issue.to_display_row()
    assert_that(row["file"]).is_equal_to("a.php")
    assert_that(row["line"]).is_equal_to("10")
    assert_that(row["code"]).is_equal_to("function.notFound")
    assert_that(row["severity"]).is_equal_to("ERROR")


def test_issue_default_severity_is_error() -> None:
    """An issue with no explicit level normalizes to ERROR severity."""
    issue = PhpstanIssue(file="a.php", line=1, message="x")
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)
