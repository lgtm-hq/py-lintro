"""Unit tests for the Stylelint parser.

The JSON payloads below are captured verbatim from stylelint 17's
``--formatter json`` output so the parser is validated against real tool
behavior (stylelint writes this payload to stderr, which lintro combines with
stdout before parsing).
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.parsers.stylelint.stylelint_parser import parse_stylelint_output

# Real capture: one CSS file, single color-hex-length violation.
SINGLE_WARNING = (
    '[{"source":"/tmp/fixme.css","deprecations":[],"invalidOptionWarnings":[],'
    '"parseErrors":[],"errored":true,"warnings":[{"line":2,"column":10,'
    '"endLine":2,"endColumn":17,"rule":"color-hex-length","severity":"error",'
    '"text":"Expected \\"#FFFFFF\\" to be \\"#FFF\\" (color-hex-length)"}]}]'
)

# Real capture: clean file (errored=false, empty warnings).
CLEAN = (
    '[{"source":"/tmp/clean.css","deprecations":[],"invalidOptionWarnings":[],'
    '"parseErrors":[],"errored":false,"warnings":[]}]'
)

# Real capture: nested SCSS with multiple rules including a duplicate.
MULTI_SCSS = (
    '[{"source":"/tmp/multi.scss","deprecations":[],"invalidOptionWarnings":[],'
    '"parseErrors":[],"errored":true,"warnings":['
    '{"line":4,"column":10,"endLine":4,"endColumn":12,"rule":"block-no-empty",'
    '"severity":"error","text":"Empty block (block-no-empty)"},'
    '{"line":2,"column":10,"endLine":2,"endColumn":17,"rule":"color-hex-length",'
    '"severity":"error","text":"Expected \\"#AABBCC\\" to be \\"#ABC\\" '
    '(color-hex-length)"},'
    '{"line":3,"column":10,"endLine":3,"endColumn":17,"rule":"color-hex-length",'
    '"severity":"error","text":"Expected \\"#AABBCC\\" to be \\"#ABC\\" '
    '(color-hex-length)"},'
    '{"line":2,"column":3,"endLine":2,"endColumn":8,'
    '"rule":"declaration-block-no-duplicate-properties","severity":"error",'
    '"text":"Duplicate property \\"color\\" '
    '(declaration-block-no-duplicate-properties)"}]}]'
)

# Real capture: syntax error surfaced as a CssSyntaxError warning.
SYNTAX_ERROR = (
    '[{"source":"/tmp/broken.css","deprecations":[],"invalidOptionWarnings":[],'
    '"parseErrors":[],"errored":true,"warnings":[{"line":1,"column":1,'
    '"rule":"CssSyntaxError","severity":"error",'
    '"text":"Unclosed block (CssSyntaxError)"}]}]'
)


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(None, id="none_input"),
        pytest.param("", id="empty_string"),
        pytest.param("   \n\n  ", id="whitespace_only"),
    ],
)
def test_parse_empty_cases(output: str | None) -> None:
    """Parser returns an empty list for empty/None input."""
    assert_that(parse_stylelint_output(output)).is_empty()


@pytest.mark.parametrize(
    "output",
    [
        pytest.param("not json at all", id="no_delimiters"),
        pytest.param("[not valid json}", id="malformed_json"),
        pytest.param("{}", id="json_object_not_array"),
        pytest.param(
            "ConfigurationError: No configuration provided for /tmp/x.css",
            id="config_error",
        ),
    ],
)
def test_parse_invalid_input(output: str) -> None:
    """Parser is resilient to malformed or non-array input."""
    assert_that(parse_stylelint_output(output)).is_empty()


def test_parse_clean_output() -> None:
    """A clean file with no warnings yields no issues."""
    assert_that(parse_stylelint_output(CLEAN)).is_empty()


def test_parse_single_warning_fields() -> None:
    """All fields are extracted from a single warning."""
    issues = parse_stylelint_output(SINGLE_WARNING)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue).is_instance_of(StylelintIssue)
    assert_that(issue.file).is_equal_to("/tmp/fixme.css")
    assert_that(issue.line).is_equal_to(2)
    assert_that(issue.column).is_equal_to(10)
    assert_that(issue.code).is_equal_to("color-hex-length")
    assert_that(issue.severity).is_equal_to("error")
    assert_that(issue.fixable).is_false()
    assert_that(issue.message).contains("#FFF")


def test_parse_multiple_scss_warnings() -> None:
    """Nested SCSS produces one issue per warning in order."""
    issues = parse_stylelint_output(MULTI_SCSS)

    assert_that(issues).is_length(4)
    codes = [i.code for i in issues]
    assert_that(codes).contains("block-no-empty")
    assert_that(codes).contains("declaration-block-no-duplicate-properties")
    assert_that(codes.count("color-hex-length")).is_equal_to(2)
    assert_that([i.file for i in issues]).contains_only("/tmp/multi.scss")


def test_parse_syntax_error_pseudo_rule() -> None:
    """CssSyntaxError syntax failures are surfaced as issues."""
    issues = parse_stylelint_output(SYNTAX_ERROR)

    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("CssSyntaxError")
    assert_that(issues[0].line).is_equal_to(1)
    assert_that(issues[0].message).contains("Unclosed block")


def test_parse_ignores_surrounding_noise() -> None:
    """JSON is extracted even when wrapped in surrounding log lines."""
    noisy = f"some warning line\n{SINGLE_WARNING}\ntrailing text"
    issues = parse_stylelint_output(noisy)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("color-hex-length")


def test_parse_warning_missing_text_is_skipped() -> None:
    """Warnings without message text are ignored."""
    output = (
        '[{"source":"/tmp/x.css","warnings":['
        '{"line":1,"column":1,"rule":"color-hex-length","severity":"error"}]}]'
    )
    assert_that(parse_stylelint_output(output)).is_empty()


def test_parse_multiple_sources() -> None:
    """Warnings across multiple source files are all collected."""
    output = "[" + SINGLE_WARNING[1:-1] + "," + MULTI_SCSS[1:-1] + "]"
    issues = parse_stylelint_output(output)
    assert_that(issues).is_length(5)
    assert_that({i.file for i in issues}).is_equal_to(
        {"/tmp/fixme.css", "/tmp/multi.scss"},
    )


def test_parse_parse_errors_array() -> None:
    """Entries in the parseErrors array are surfaced as issues."""
    output = (
        '[{"source":"/tmp/x.css","warnings":[],"parseErrors":['
        '{"line":5,"column":2,"text":"Unexpected token"}]}]'
    )
    issues = parse_stylelint_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("parseError")
    assert_that(issues[0].line).is_equal_to(5)


def test_issue_display_row_severity_and_doc_key() -> None:
    """StylelintIssue maps native severity into the unified display row."""
    issue = StylelintIssue(
        file="a.css",
        line=1,
        column=2,
        code="color-hex-length",
        message="msg",
        severity="warning",
    )
    row = issue.to_display_row()
    assert_that(row["severity"]).is_equal_to("WARNING")
    assert_that(row["code"]).is_equal_to("color-hex-length")
    assert_that(row["fixable"]).is_equal_to("")


def test_invalid_option_warnings_are_reported() -> None:
    """Entries in invalidOptionWarnings surface as issues, not silence."""
    output = (
        '[{"source":"/tmp/a.css","warnings":[],'
        '"invalidOptionWarnings":[{"text":'
        '"Invalid option value \\"tabs\\" for rule \\"indentation\\""}]}]'
    )
    issues = parse_stylelint_output(output=output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("invalidOption")
    assert_that(issues[0].message).contains("Invalid option")


def test_bracketed_log_line_before_json_is_tolerated() -> None:
    """A bracketed warning line before the payload does not defeat parsing."""
    output = (
        "[Warning] something noisy\n"
        '[{"source":"/tmp/a.css","warnings":[{"line":1,"column":1,'
        '"rule":"block-no-empty","severity":"error","text":"empty"}]}]'
    )
    issues = parse_stylelint_output(output=output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("block-no-empty")
