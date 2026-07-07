"""Unit tests for the SwiftLint parser.

Fixtures use output captured from a real ``swiftlint lint --reporter json``
run against ``test_samples/tools/swift/swiftlint/swiftlint_violations.swift``.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.swiftlint.swiftlint_issue import SwiftlintIssue
from lintro.parsers.swiftlint.swiftlint_parser import parse_swiftlint_output

# Real captured output (abridged to two representative violations).
REAL_OUTPUT = """[
  {
    "character" : 9,
    "file" : "/abs/Sample.swift",
    "line" : 5,
    "reason" : "Variable name 'x' should be between 3 and 40 characters long",
    "rule_id" : "identifier_name",
    "severity" : "Error",
    "type" : "Identifier Name"
  },
  {
    "character" : 1,
    "file" : "/abs/Sample.swift",
    "line" : 7,
    "reason" : "Line should be 120 characters or less; currently it has 128 characters",
    "rule_id" : "line_length",
    "severity" : "Warning",
    "type" : "Line Length"
  }
]"""


def test_parse_real_output_returns_all_issues() -> None:
    """Parser returns one issue per element in the JSON array."""
    issues = parse_swiftlint_output(REAL_OUTPUT)
    assert_that(issues).is_length(2)


def test_parse_extracts_location_and_rule_fields() -> None:
    """Parser maps file, line, character->column, rule_id->code, reason."""
    issues = parse_swiftlint_output(REAL_OUTPUT)
    first = issues[0]
    assert_that(first.file).is_equal_to("/abs/Sample.swift")
    assert_that(first.line).is_equal_to(5)
    assert_that(first.column).is_equal_to(9)
    assert_that(first.code).is_equal_to("identifier_name")
    assert_that(first.message).contains("Variable name 'x'")
    assert_that(first.rule_type).is_equal_to("Identifier Name")


def test_parse_extracts_severity_levels() -> None:
    """Parser preserves the raw severity string per issue."""
    issues = parse_swiftlint_output(REAL_OUTPUT)
    assert_that(issues[0].level).is_equal_to("Error")
    assert_that(issues[1].level).is_equal_to("Warning")


def test_severity_normalization_maps_to_enum() -> None:
    """Raw SwiftLint severities normalize to lintro's SeverityLevel."""
    issues = parse_swiftlint_output(REAL_OUTPUT)
    assert_that(issues[0].get_severity()).is_equal_to(SeverityLevel.ERROR)
    assert_that(issues[1].get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_display_row_routes_level_to_severity() -> None:
    """DISPLAY_FIELD_MAP routes ``level`` into the severity display column."""
    issues = parse_swiftlint_output(REAL_OUTPUT)
    row = issues[0].to_display_row()
    assert_that(row["code"]).is_equal_to("identifier_name")
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.ERROR))
    assert_that(row["line"]).is_equal_to("5")
    assert_that(row["column"]).is_equal_to("9")


def test_parse_none_returns_empty_list() -> None:
    """None input yields an empty list, not an error."""
    assert_that(parse_swiftlint_output(None)).is_empty()


def test_parse_empty_string_returns_empty_list() -> None:
    """Empty/whitespace input yields an empty list."""
    assert_that(parse_swiftlint_output("")).is_empty()
    assert_that(parse_swiftlint_output("   \n  ")).is_empty()


def test_parse_empty_json_array_returns_empty_list() -> None:
    """A clean run emits an empty JSON array."""
    assert_that(parse_swiftlint_output("[\n\n]")).is_empty()


def test_parse_malformed_json_returns_empty_list() -> None:
    """Malformed JSON is tolerated and yields an empty list."""
    assert_that(parse_swiftlint_output("[{not json")).is_empty()


def test_parse_non_array_json_returns_empty_list() -> None:
    """A JSON object (non-array) root yields an empty list."""
    assert_that(parse_swiftlint_output('{"file": "a.swift"}')).is_empty()


def test_parse_skips_non_dict_elements() -> None:
    """Non-dict array elements are skipped, valid ones retained."""
    output = (
        '[42, "str", '
        '{"file": "a.swift", "line": 3, "character": 2, '
        '"severity": "Warning", "type": "Line Length", '
        '"rule_id": "line_length", "reason": "too long"}]'
    )
    issues = parse_swiftlint_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("line_length")


def test_parse_missing_fields_use_defaults() -> None:
    """Missing optional fields fall back to safe defaults."""
    issues = parse_swiftlint_output('[{"rule_id": "todo"}]')
    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.code).is_equal_to("todo")
    assert_that(issue.file).is_equal_to("")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.level).is_none()


def test_parse_non_numeric_location_defaults_to_zero() -> None:
    """Non-numeric line/character values degrade to 0 rather than raising."""
    output = (
        '[{"file": "a.swift", "line": "x", "character": null, '
        '"rule_id": "r", "reason": "m"}]'
    )
    issues = parse_swiftlint_output(output)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)


def test_issue_model_defaults() -> None:
    """The issue dataclass exposes swiftlint-specific fields with defaults."""
    issue = SwiftlintIssue()
    assert_that(issue.code).is_equal_to("")
    assert_that(issue.level).is_none()
    assert_that(issue.rule_type).is_none()
    # No native severity -> falls back to BaseIssue default (WARNING).
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.WARNING)
