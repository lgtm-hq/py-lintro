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


def test_non_list_path_is_empty() -> None:
    """A non-array ``path`` field is treated as document-level (empty)."""
    output = (
        '[{"code": "c", "path": "not-a-list", "message": "m", "severity": 1,'
        ' "source": "f.yaml"}]'
    )
    issue = parse_spectral_output(output)[0]
    assert_that(issue.path).is_empty()


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


def test_missing_range_is_unknown_location() -> None:
    """A finding with no range uses line 0, column 0 (unknown)."""
    output = (
        '[{"code": "c", "path": [], "message": "m", "severity": 1, "source": "f.yaml"}]'
    )
    issue = parse_spectral_output(output)[0]
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)


def test_null_range_offsets_are_unknown() -> None:
    """JSON null line/character values must not raise or become 1:1."""
    output = (
        '[{"code": "c", "path": [], "message": "m", "severity": 1,'
        ' "range": {"start": {"line": null, "character": null}},'
        ' "source": "f.yaml"}]'
    )
    issue = parse_spectral_output(output)[0]
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)


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


def test_bracketed_stderr_preamble_is_tolerated() -> None:
    """A bracketed warning line before the array does not defeat parsing."""
    output = "[Warning] ruleset resolution was slow\n" + REAL_OUTPUT
    assert_that(parse_spectral_output(output)).is_length(2)


def test_null_fields_do_not_become_literal_none() -> None:
    """JSON nulls map to empty strings, not the string 'None'."""
    output = (
        '[{"code": "c", "message": "m", "source": null,'
        ' "severity": 1, "range": {"start": {"line": 0, "character": 0}}}]'
    )
    issues = parse_spectral_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).is_empty()
    assert_that(issues[0].code).is_equal_to("c")
    assert_that(issues[0].message).is_equal_to("m")


def test_empty_code_and_message_is_skipped() -> None:
    """A finding with no code and no message is not a blank 1:1 warning."""
    output = '[{"severity": 1, "source": "f.yaml"}]'
    assert_that(parse_spectral_output(output)).is_empty()


def test_noise_json_array_does_not_hide_findings() -> None:
    """A non-finding JSON array before the payload does not win the scan."""
    output = '["warning"]\n' + REAL_OUTPUT
    assert_that(parse_spectral_output(output)).is_length(2)


def test_display_row_includes_json_path_in_message() -> None:
    """The JSON path is visible in the unified display row."""
    issue = parse_spectral_output(REAL_OUTPUT)[1]
    row = issue.to_display_row()
    assert_that(row["message"]).contains("paths./users.get")
    assert_that(row["path"]).is_equal_to("paths./users.get")
