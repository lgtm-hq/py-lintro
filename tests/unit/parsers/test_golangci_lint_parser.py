"""Unit tests for the golangci-lint parser.

Fixtures mirror real golangci-lint v2 ``--output.json.path stdout`` payloads
captured from a Go module fixture.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.parsers.golangci_lint.golangci_lint_parser import (
    parse_golangci_lint_output,
)

# Real captured output: two findings (errcheck + ineffassign), empty Severity.
_TWO_ISSUES = (
    '{"Issues":[{"FromLinter":"errcheck",'
    '"Text":"Error return value of `os.Open` is not checked","Severity":"",'
    '"SourceLines":["\\tos.Open(\\"foo.txt\\")"],'
    '"Pos":{"Filename":"main.go","Offset":61,"Line":9,"Column":9},'
    '"ExpectNoLint":false,"ExpectedNoLintLinter":""},'
    '{"FromLinter":"ineffassign","Text":"ineffectual assignment to x",'
    '"Severity":"","SourceLines":["\\tx := 1"],'
    '"Pos":{"Filename":"main.go","Offset":74,"Line":10,"Column":2},'
    '"ExpectNoLint":false,"ExpectedNoLintLinter":""}],'
    '"Report":{"Linters":[{"Name":"errcheck","Enabled":true}]}}'
)


def test_parse_single_issue_fields() -> None:
    """Parser extracts linter, position, and message for a single issue."""
    output = (
        '{"Issues":[{"FromLinter":"ineffassign",'
        '"Text":"ineffectual assignment to x","Severity":"",'
        '"Pos":{"Filename":"main.go","Offset":82,"Line":10,"Column":2}}],'
        '"Report":{"Linters":[]}}'
    )
    issues = parse_golangci_lint_output(output)
    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue.file).is_equal_to("main.go")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.column).is_equal_to(2)
    assert_that(issue.code).is_equal_to("ineffassign")
    assert_that(issue.message).contains("ineffectual assignment")
    assert_that(issue.fixable).is_false()


def test_parse_multiple_issues() -> None:
    """Parser returns every issue with correct linter attribution."""
    issues = parse_golangci_lint_output(_TWO_ISSUES)
    assert_that(issues).is_length(2)
    assert_that([i.code for i in issues]).is_equal_to(["errcheck", "ineffassign"])
    assert_that(issues[0].line).is_equal_to(9)
    assert_that(issues[1].line).is_equal_to(10)


def test_parse_returns_issue_instances() -> None:
    """Parsed items are GolangciLintIssue dataclass instances."""
    issues = parse_golangci_lint_output(_TWO_ISSUES)
    assert_that(issues[0]).is_instance_of(GolangciLintIssue)


def test_empty_severity_defaults_to_warning() -> None:
    """A blank Severity normalizes to the WARNING default."""
    issues = parse_golangci_lint_output(_TWO_ISSUES)
    assert_that(issues[0].level).is_none()
    assert_that(issues[0].get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_explicit_severity_is_preserved() -> None:
    """A configured Severity value is retained and normalized."""
    output = (
        '{"Issues":[{"FromLinter":"gosec",'
        '"Text":"potential security issue","Severity":"error",'
        '"Pos":{"Filename":"main.go","Line":3,"Column":1}}],'
        '"Report":{"Linters":[]}}'
    )
    issues = parse_golangci_lint_output(output)
    assert_that(issues[0].level).is_equal_to("error")
    assert_that(issues[0].get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_suggested_fixes_marks_fixable() -> None:
    """Presence of SuggestedFixes marks the issue as fixable."""
    output = (
        '{"Issues":[{"FromLinter":"misspell",'
        '"Text":"`langauge` is a misspelling of `language`","Severity":"",'
        '"Pos":{"Filename":"main.go","Line":5,"Column":14},'
        '"SuggestedFixes":[{"Message":"",'
        '"TextEdits":[{"Pos":41,"End":49,"NewText":"bGFuZ3VhZ2U="}]}]}],'
        '"Report":{"Linters":[]}}'
    )
    issues = parse_golangci_lint_output(output)
    assert_that(issues[0].fixable).is_true()


def test_legacy_replacement_marks_fixable() -> None:
    """The legacy Replacement field also marks the issue as fixable."""
    output = (
        '{"Issues":[{"FromLinter":"gofmt","Text":"File is not gofmted",'
        '"Severity":"","Pos":{"Filename":"main.go","Line":1,"Column":1},'
        '"Replacement":{"NewLines":["package main"]}}],'
        '"Report":{"Linters":[]}}'
    )
    issues = parse_golangci_lint_output(output)
    assert_that(issues[0].fixable).is_true()


def test_no_issues_returns_empty() -> None:
    """A clean run with an empty Issues array yields no issues."""
    output = '{"Issues":[],"Report":{"Linters":[]}}'
    assert_that(parse_golangci_lint_output(output)).is_empty()


def test_null_issues_returns_empty() -> None:
    """golangci-lint emits ``"Issues":null`` for a clean run in some versions."""
    output = '{"Issues":null,"Report":{"Linters":[]}}'
    assert_that(parse_golangci_lint_output(output)).is_empty()


def test_trailing_stats_footer_is_ignored() -> None:
    """A human-readable stats footer after the JSON is tolerated."""
    output = _TWO_ISSUES + "\n2 issues:\n* errcheck: 1\n* ineffassign: 1\n"
    issues = parse_golangci_lint_output(output)
    assert_that(issues).is_length(2)


def test_ansi_codes_are_stripped() -> None:
    """ANSI escape codes around the JSON are stripped before parsing."""
    output = "\x1b[33m" + _TWO_ISSUES + "\x1b[0m"
    issues = parse_golangci_lint_output(output)
    assert_that(issues).is_length(2)


def test_empty_and_none_input() -> None:
    """Empty, whitespace, and None inputs return an empty list."""
    assert_that(parse_golangci_lint_output(None)).is_empty()
    assert_that(parse_golangci_lint_output("")).is_empty()
    assert_that(parse_golangci_lint_output("   \n  ")).is_empty()


def test_malformed_json_returns_empty() -> None:
    """Malformed JSON is handled gracefully without raising."""
    assert_that(parse_golangci_lint_output("not json at all")).is_empty()
    assert_that(parse_golangci_lint_output("{broken")).is_empty()


def test_non_object_root_returns_empty() -> None:
    """A JSON array root (not the expected object) returns an empty list."""
    assert_that(parse_golangci_lint_output("[1, 2, 3]")).is_empty()


def test_issue_missing_position_gets_module_placeholder() -> None:
    """An issue without a filename is kept with a module-level placeholder.

    Package-level build/config/analysis failures carry no position; dropping
    them would hide a real failure from the report.
    """
    output = (
        '{"Issues":[{"FromLinter":"errcheck","Text":"no position"},'
        '{"FromLinter":"ineffassign","Text":"has position",'
        '"Pos":{"Filename":"main.go","Line":1,"Column":1}}],'
        '"Report":{"Linters":[]}}'
    )
    issues = parse_golangci_lint_output(output)
    assert_that(issues).is_length(2)
    assert_that(issues[0].file).is_equal_to("(module)")
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[1].code).is_equal_to("ineffassign")


def test_issue_missing_text_is_skipped() -> None:
    """An issue with an empty message is skipped."""
    output = (
        '{"Issues":[{"FromLinter":"errcheck","Text":"",'
        '"Pos":{"Filename":"main.go","Line":1,"Column":1}}],'
        '"Report":{"Linters":[]}}'
    )
    assert_that(parse_golangci_lint_output(output)).is_empty()
