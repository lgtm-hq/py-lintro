"""Unit tests for the Stylelint parser.

The JSON payloads below are captured verbatim from stylelint 17's
``--formatter json`` output so the parser is validated against real tool
behavior (stylelint writes this payload to stderr, which lintro combines with
stdout before parsing).
"""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.parsers.stylelint.stylelint_parser import (
    parse_stylelint_output,
    parse_stylelint_output_with_stats,
)

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


@pytest.mark.parametrize("output", [CLEAN, "[]", "notice\n[]\n[Warning] done"])
def test_parse_clean_output(output: str) -> None:
    """A complete clean report remains valid with surrounding logging."""
    assert_that(parse_stylelint_output_with_stats(output)).is_equal_to(([], 0))


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


def test_parse_warning_missing_text_is_counted() -> None:
    """Warnings without message text are unusable records, not a clean report."""
    output = (
        '[{"source":"/tmp/x.css","warnings":['
        '{"line":1,"column":1,"rule":"color-hex-length","severity":"error"}]}]'
    )
    assert_that(parse_stylelint_output_with_stats(output)).is_equal_to(([], 1))


@pytest.mark.parametrize("field", ["warnings", "parseErrors", "invalidOptionWarnings"])
def test_invalid_warning_records_preserve_usable_neighbors(field: str) -> None:
    """Every malformed record counts without discarding the valid warning."""
    result: dict[str, object] = {"source": "a.css", "warnings": []}
    result[field] = [None, {"line": 1}, {"text": "kept"}]
    issues, failures = parse_stylelint_output_with_stats(json.dumps([result]))

    assert_that(failures).is_equal_to(2)
    assert_that(issues).extracting("message").is_equal_to(["kept"])


@pytest.mark.parametrize("prefix", ["Plugins: []\n", "[]\n", "[runner] []\n"])
def test_leading_empty_array_does_not_hide_report(prefix: str) -> None:
    """Prefer an actual source report to empty arrays embedded in logging."""
    issues, failures = parse_stylelint_output_with_stats(prefix + SINGLE_WARNING)

    assert_that(failures).is_zero()
    assert_that(issues).extracting("code").is_equal_to(["color-hex-length"])


@pytest.mark.parametrize(
    "prefix",
    [
        "plugin warning [unfinished\n",
        "plugin warning {unfinished\n",
        "[unfinished\n",
        "{unfinished\n",
        "plugin warning [\n",
        "plugin warning {\n",
        "plugin warning [unfinished\n" * 1000,
    ],
    ids=[
        "log-array",
        "log-object",
        "array",
        "object",
        "bare-array",
        "bare-object",
        "many",
    ],
)
def test_unmatched_log_delimiters_do_not_hide_findings(prefix: str) -> None:
    """Unmatched logging delimiters cannot swallow the actual JSON report."""
    issues, failures = parse_stylelint_output_with_stats(prefix + SINGLE_WARNING)

    assert_that(failures).is_zero()
    assert_that(issues).extracting("file", "line", "code").is_equal_to(
        [("/tmp/fixme.css", 2, "color-hex-length")],
    )


def test_noisy_extraction_bounds_json_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recover findings without repeatedly decoding long overlapping suffixes."""
    output = (
        '["warning"]\n' * 1000 + "plugin warning [unfinished\n" * 1000 + SINGLE_WARNING
    )
    original_decode = json.JSONDecoder.raw_decode
    decoded_chars = 0

    def counted_decode(
        self: json.JSONDecoder,
        document: str,
        idx: int = 0,
    ) -> tuple[object, int]:
        nonlocal decoded_chars
        decoded_chars += len(document) - idx
        return original_decode(self, document, idx)

    with monkeypatch.context() as context:
        context.setattr(json.JSONDecoder, "raw_decode", counted_decode)
        issues, failures = parse_stylelint_output_with_stats(output)

    assert_that(failures).is_zero()
    assert_that(issues).extracting("code").is_equal_to(["color-hex-length"])
    assert_that(decoded_chars).is_positive().is_less_than_or_equal_to(3 * len(output))


@pytest.mark.parametrize(
    "invalid",
    ["[null]", "[{}]", "[false]", "[[[]]]", '{"warnings":[]}'],
)
@pytest.mark.parametrize("empty_first", [False, True])
def test_invalid_report_prevents_empty_array_fallback(
    invalid: str,
    empty_first: bool,
) -> None:
    """A standalone empty array cannot mask an invalid outer report."""
    reports = ["[]", invalid] if empty_first else [invalid, "[]"]
    output = "\nnotice\n".join(reports)

    assert_that(parse_stylelint_output_with_stats(output)).is_equal_to(([], 1))


@pytest.mark.parametrize(
    "output",
    [
        "Plugins: []",
        'notice\n[{"source":"a.css","warnings":[]',
        '[]\nnotice\n[{"source":"a.css","warnings":[]',
        'notice\n{"other":{"warnings":[]}}',
        "notice\n[\n[]",
        'notice\n{\n"warnings":[]',
        "notice\n[\n" + SINGLE_WARNING,
        'notice\n{"nested":\n' + SINGLE_WARNING,
        "[]\nnotice\n[null}",
    ],
)
def test_noise_or_truncated_report_cannot_expose_empty_arrays(output: str) -> None:
    """Only a complete outer report can establish a clean result."""
    assert_that(parse_stylelint_output_with_stats(output)).is_equal_to(([], 1))


def test_noisy_report_preserves_malformed_source_accounting() -> None:
    """A usable sibling enables extraction while the malformed entry counts."""
    output = 'notice\n[1, {"source":"a.css","warnings":[{"text":"kept"}]}]'
    issues, failures = parse_stylelint_output_with_stats(output)

    assert_that(failures).is_equal_to(1)
    assert_that(issues).extracting("message").is_equal_to(["kept"])


@pytest.mark.parametrize(
    "invalid",
    ["[null]", "[{}]", "[false]", "[[[]]]", '{"warnings":[]}', "[null}"],
)
@pytest.mark.parametrize("invalid_first", [False, True])
@pytest.mark.parametrize("report", [CLEAN, SINGLE_WARNING])
def test_invalid_outer_sibling_is_counted_with_usable_report(
    invalid: str,
    invalid_first: bool,
    report: str,
) -> None:
    """Recognizable reports retain findings and failures in either order."""
    reports = [invalid, report] if invalid_first else [report, invalid]
    issues, failures = parse_stylelint_output_with_stats("\n".join(reports))

    assert_that(failures).is_equal_to(1)
    assert_that(issues).extracting("code").is_equal_to(
        ["color-hex-length"] if report == SINGLE_WARNING else [],
    )


@pytest.mark.parametrize(
    "scalar",
    ["null", "false", "true", "42", "-1.5e2", '"scalar"', '"[]"', '"["'],
)
@pytest.mark.parametrize("scalar_first", [False, True])
@pytest.mark.parametrize("report", ["[]", CLEAN, SINGLE_WARNING])
def test_standalone_scalar_siblings_are_invalid_reports(
    scalar: str,
    scalar_first: bool,
    report: str,
) -> None:
    """A scalar on its own line counts without hiding neighboring findings."""
    reports = [scalar, report] if scalar_first else [report, scalar]
    issues, failures = parse_stylelint_output_with_stats(
        "\nnotice\n".join(reports),
    )

    assert_that(failures).is_equal_to(1)
    assert_that(issues).extracting("file", "line", "code").is_equal_to(
        [("/tmp/fixme.css", 2, "color-hex-length")] if report == SINGLE_WARNING else [],
    )


def test_nested_scalars_and_inline_log_values_are_not_outer_reports() -> None:
    """Only standalone outer scalars add failures during noisy recovery."""
    output = "\n".join(
        [
            "notice null false 42",
            'plugin value: "[]"',
            '[\nnull\n,{"source":"a.css","warnings":[{"text":\n"kept"\n}]}\n]',
        ],
    )
    issues, failures = parse_stylelint_output_with_stats(output)

    assert_that(failures).is_equal_to(1)
    assert_that(issues).extracting("message").is_equal_to(["kept"])


def test_scalar_extraction_accumulates_failures_with_bounded_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count every scalar once while keeping total decoded input linear."""
    output = ('  "["  \nnull\n' * 1000) + SINGLE_WARNING + ("\nfalse" * 1000)
    original_decode = json.JSONDecoder.raw_decode
    decoded_chars = 0

    def counted_decode(
        self: json.JSONDecoder,
        document: str,
        idx: int = 0,
    ) -> tuple[object, int]:
        nonlocal decoded_chars
        decoded_chars += len(document) - idx
        return original_decode(self, document, idx)

    with monkeypatch.context() as context:
        context.setattr(json.JSONDecoder, "raw_decode", counted_decode)
        issues, failures = parse_stylelint_output_with_stats(output)

    assert_that(failures).is_equal_to(3000)
    assert_that(issues).extracting("code").is_equal_to(["color-hex-length"])
    assert_that(decoded_chars).is_positive().is_less_than_or_equal_to(3 * len(output))


def test_later_reports_keep_findings_and_accumulate_failures() -> None:
    """Every outer sibling contributes even after the first usable report."""
    output = "\n".join(["[null]", CLEAN, SINGLE_WARNING, "[false]", "[{", ""])
    issues, failures = parse_stylelint_output_with_stats(output)

    assert_that(failures).is_equal_to(3)
    assert_that(issues).extracting("file", "code").is_equal_to(
        [("/tmp/fixme.css", "color-hex-length")],
    )


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


def test_unrelated_json_array_before_payload_is_skipped() -> None:
    """A valid but non-Stylelint JSON array before the payload is ignored."""
    output = (
        '["warning"]\n'
        '[{"type":"warning"}]\n'
        '[{"source":"/tmp/a.css","warnings":[{"line":1,"column":1,'
        '"rule":"block-no-empty","severity":"error","text":"empty"}]}]'
    )
    issues, failures = parse_stylelint_output_with_stats(output=output)
    assert_that(failures).is_zero()
    assert_that(issues).is_length(1)
    assert_that(issues[0].code).is_equal_to("block-no-empty")
