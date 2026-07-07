"""Unit tests for the Spectral parser.

Fixtures use real output captured from ``spectral lint --format json`` (v6.16.1)
against a minimal OpenAPI 3.0 document linted with ``spectral:oas``.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.parsers.spectral.spectral_parser import parse_spectral_output

# Real capture: two findings from spectral:oas on a minimal OpenAPI 3.0 spec.
# Note spectral reports zero-based line/character offsets.
REAL_OUTPUT = """[
  {
    "code": "oas3-api-servers",
    "path": [],
    "message": "OpenAPI \\"servers\\" must be present and non-empty array.",
    "severity": 1,
    "range": {
      "start": {"line": 0, "character": 0},
      "end": {"line": 9, "character": 25}
    },
    "source": "/repo/openapi.yaml"
  },
  {
    "code": "operation-operationId",
    "path": ["paths", "/users", "get"],
    "message": "Operation must have \\"operationId\\".",
    "severity": 1,
    "range": {
      "start": {"line": 6, "character": 8},
      "end": {"line": 9, "character": 25}
    },
    "source": "/repo/openapi.yaml"
  }
]"""


def test_parse_empty_output() -> None:
    """Empty output yields no issues."""
    assert_that(parse_spectral_output("")).is_empty()


def test_parse_none_output() -> None:
    """None output yields no issues."""
    assert_that(parse_spectral_output(None)).is_empty()


def test_parse_whitespace_output() -> None:
    """Whitespace-only output yields no issues."""
    assert_that(parse_spectral_output("   \n  ")).is_empty()


def test_parse_malformed_json() -> None:
    """Malformed JSON yields no issues (defensive)."""
    assert_that(parse_spectral_output("[{invalid")).is_empty()


def test_parse_non_list_json() -> None:
    """A JSON object (not an array) yields no issues."""
    assert_that(parse_spectral_output('{"code": "x"}')).is_empty()


def test_parse_no_brackets() -> None:
    """Output without a JSON array yields no issues."""
    assert_that(parse_spectral_output("No ruleset has been found.")).is_empty()


def test_parse_real_output_count() -> None:
    """Real output parses to the expected number of findings."""
    issues = parse_spectral_output(REAL_OUTPUT)
    assert_that(issues).is_length(2)


def test_parse_extracts_code_and_message() -> None:
    """Rule code and message are extracted."""
    issues = parse_spectral_output(REAL_OUTPUT)
    assert_that(issues[0].code).is_equal_to("oas3-api-servers")
    assert_that(issues[0].message).contains("servers")


def test_parse_converts_line_and_column_to_one_based() -> None:
    """Zero-based offsets are converted to one-based line/column."""
    issues = parse_spectral_output(REAL_OUTPUT)
    # start line 0, character 0 -> line 1, column 1
    assert_that(issues[0].line).is_equal_to(1)
    assert_that(issues[0].column).is_equal_to(1)
    # start line 6, character 8 -> line 7, column 9
    assert_that(issues[1].line).is_equal_to(7)
    assert_that(issues[1].column).is_equal_to(9)


def test_parse_joins_json_path() -> None:
    """The JSON path array is joined into a dotted string."""
    issues = parse_spectral_output(REAL_OUTPUT)
    assert_that(issues[0].path).is_equal_to("")
    assert_that(issues[1].path).is_equal_to("paths./users.get")


def test_parse_extracts_source_file() -> None:
    """The source file path is extracted."""
    issues = parse_spectral_output(REAL_OUTPUT)
    assert_that(issues[0].file).is_equal_to("/repo/openapi.yaml")


def test_severity_level_mapping() -> None:
    """Integer severity levels map to the expected severity strings."""
    template = (
        '[{{"code": "c", "path": [], "message": "m", "severity": {level}, '
        '"range": {{"start": {{"line": 0, "character": 0}}}}, '
        '"source": "f.yaml"}}]'
    )
    assert_that(
        parse_spectral_output(template.format(level=0))[0].severity,
    ).is_equal_to(
        "error",
    )
    assert_that(
        parse_spectral_output(template.format(level=1))[0].severity,
    ).is_equal_to(
        "warning",
    )
    assert_that(
        parse_spectral_output(template.format(level=2))[0].severity,
    ).is_equal_to(
        "info",
    )
    assert_that(
        parse_spectral_output(template.format(level=3))[0].severity,
    ).is_equal_to(
        "hint",
    )


def test_error_severity_normalizes() -> None:
    """A level-0 finding normalizes to ERROR severity."""
    output = (
        '[{"code": "oas3-schema", "path": [], "message": "bad", "severity": 0, '
        '"range": {"start": {"line": 3, "character": 0}}, "source": "f.yaml"}]'
    )
    issue = parse_spectral_output(output)[0]
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_hint_severity_normalizes_to_info() -> None:
    """A level-3 (hint) finding normalizes to INFO severity."""
    output = (
        '[{"code": "custom", "path": [], "message": "hint", "severity": 3, '
        '"range": {"start": {"line": 0, "character": 0}}, "source": "f.yaml"}]'
    )
    issue = parse_spectral_output(output)[0]
    assert_that(issue.get_severity()).is_equal_to(SeverityLevel.INFO)


def test_missing_range_defaults_to_one_one() -> None:
    """A finding with no range defaults to line 1, column 1."""
    output = (
        '[{"code": "c", "path": [], "message": "m", "severity": 1, "source": "f.yaml"}]'
    )
    issue = parse_spectral_output(output)[0]
    assert_that(issue.line).is_equal_to(1)
    assert_that(issue.column).is_equal_to(1)


def test_non_dict_entries_skipped() -> None:
    """Non-object array entries are skipped without error."""
    output = '["not a dict", 42, null]'
    assert_that(parse_spectral_output(output)).is_empty()


def test_preamble_before_json_is_tolerated() -> None:
    """A non-JSON preamble before the array is tolerated."""
    output = "Some warning line\n" + REAL_OUTPUT
    assert_that(parse_spectral_output(output)).is_length(2)


def test_issue_is_spectral_issue() -> None:
    """Parsed entries are SpectralIssue instances."""
    issues = parse_spectral_output(REAL_OUTPUT)
    assert_that(issues[0]).is_instance_of(SpectralIssue)


def test_display_row_exposes_code_and_severity() -> None:
    """The display row surfaces the rule code and severity."""
    issue = parse_spectral_output(REAL_OUTPUT)[0]
    row = issue.to_display_row()
    assert_that(row["code"]).is_equal_to("oas3-api-servers")
    assert_that(row["severity"]).is_equal_to(str(SeverityLevel.WARNING))
