"""Tests for the canonical finding/summary serializer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.severity_level import SeverityLevel
from lintro.enums.tool_run_status import ToolRunStatus
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.findings import (
    FindingScope,
    finding_from_issue,
    findings_from_results,
    issues_for_result,
    tool_summaries_from_results,
    tool_summary_from_result,
)


@dataclass
class _FixableIssue(BaseIssue):
    """Issue carrying a code, a severity, and a fixable flag."""

    code: str = ""
    severity: str = "error"
    fixable: bool = False


@dataclass
class _RemappedIssue(BaseIssue):
    """Issue that remaps ``code`` and ``severity`` onto its own attributes."""

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        "code": "rule_id",
        "severity": "level",
        "fixable": "auto_fix",
        "message": "message",
    }

    rule_id: str = ""
    level: str = "note"
    auto_fix: bool = True


def _issue(**overrides: object) -> _FixableIssue:
    """Build a fixable issue with sensible defaults.

    Args:
        **overrides: Field values to override.

    Returns:
        _FixableIssue: The constructed issue.
    """
    defaults: dict[str, object] = {
        "file": "src/app.py",
        "line": 3,
        "column": 7,
        "message": "bad thing",
        "code": "F401",
        "severity": "error",
        "fixable": True,
    }
    defaults.update(overrides)
    return _FixableIssue(**defaults)  # type: ignore[arg-type]


def test_finding_from_issue_captures_every_field() -> None:
    """A finding carries the full superset of issue fields."""
    finding = finding_from_issue(
        issue=_issue(doc_url="https://example.test/F401"),
        tool_name="ruff",
    )

    assert_that(finding.tool).is_equal_to("ruff")
    assert_that(finding.file).is_equal_to("src/app.py")
    assert_that(finding.line).is_equal_to(3)
    assert_that(finding.column).is_equal_to(7)
    assert_that(finding.rule).is_equal_to("F401")
    assert_that(finding.severity).is_equal_to(SeverityLevel.ERROR)
    assert_that(finding.message).is_equal_to("bad thing")
    assert_that(finding.fixable).is_true()
    assert_that(finding.doc_url).is_equal_to("https://example.test/F401")


def test_finding_from_issue_follows_display_field_remapping() -> None:
    """Rule, severity, and fixable resolve through DISPLAY_FIELD_MAP."""
    issue = _RemappedIssue(
        file="a.ts",
        line=1,
        column=2,
        message="remapped",
        rule_id="no-explicit-any",
        level="note",
        auto_fix=True,
    )

    finding = finding_from_issue(issue=issue, tool_name="oxlint")

    assert_that(finding.rule).is_equal_to("no-explicit-any")
    assert_that(finding.severity).is_equal_to(SeverityLevel.INFO)
    assert_that(finding.fixable).is_true()


def test_finding_to_dict_omits_empty_doc_url() -> None:
    """The wire payload drops doc_url when the tool supplied none."""
    payload = finding_from_issue(issue=_issue(), tool_name="ruff").to_dict()

    assert_that(payload).does_not_contain_key("doc_url")
    assert_that(payload["severity"]).is_equal_to("ERROR")
    assert_that(payload["fixable"]).is_true()


def test_check_run_reports_issues_verbatim() -> None:
    """A check run reports exactly the issues the tool parsed."""
    result = ToolResult(name="ruff", success=False, issues=[_issue(), _issue()])

    findings = findings_from_results(results=[result], action=Action.CHECK)

    assert_that(findings).is_length(2)
    assert_that({finding.tool for finding in findings}).is_equal_to({"ruff"})


def test_fix_run_all_scope_merges_detected_and_remaining() -> None:
    """FindingScope.ALL unions the pre-fix detections with the remainder."""
    fixed = _issue(code="F401")
    remaining = _issue(code="E501", fixable=False)
    result = ToolResult(
        name="ruff",
        success=True,
        issues=[fixed, remaining],
        initial_issues=[fixed, remaining],
        initial_issues_count=2,
        fixed_issues_count=1,
        remaining_issues_count=1,
    )

    findings = findings_from_results(
        results=[result],
        action=Action.FIX,
        scope=FindingScope.ALL,
    )

    assert_that([finding.rule for finding in findings]).is_equal_to(["F401", "E501"])


def test_fix_run_remaining_scope_takes_the_tail() -> None:
    """FindingScope.REMAINING keeps only what the fix run could not resolve."""
    fixed = _issue(code="F401")
    remaining = _issue(code="E501", fixable=False)
    result = ToolResult(
        name="ruff",
        success=True,
        issues=[fixed, remaining],
        initial_issues=[fixed, remaining],
        initial_issues_count=2,
        fixed_issues_count=1,
        remaining_issues_count=1,
    )

    findings = findings_from_results(
        results=[result],
        action=Action.FIX,
        scope=FindingScope.REMAINING,
    )

    assert_that([finding.rule for finding in findings]).is_equal_to(["E501"])


def test_fix_run_remaining_scope_is_empty_when_all_fixed() -> None:
    """Nothing remains when the tool fixed everything it found."""
    result = ToolResult(
        name="ruff",
        success=True,
        issues=[_issue()],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
    )

    issues = issues_for_result(
        result=result,
        action=Action.FIX,
        scope=FindingScope.REMAINING,
    )

    assert_that(issues).is_empty()


def test_fix_run_remaining_scope_falls_back_without_counts() -> None:
    """A tool that reports no remaining count is treated as all-remaining."""
    result = ToolResult(name="prettier", success=False, issues=[_issue()])

    issues = issues_for_result(
        result=result,
        action=Action.FIX,
        scope=FindingScope.REMAINING,
    )

    assert_that(issues).is_length(1)


def test_tool_summary_reports_issues_status_and_duration() -> None:
    """A tool with findings reports the issues status and its duration."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues=[_issue()],
        issues_count=1,
        duration_seconds=1.25,
    )

    summary = tool_summary_from_result(result=result, action=Action.CHECK)

    assert_that(summary.tool).is_equal_to("ruff")
    assert_that(summary.status).is_equal_to(ToolRunStatus.ISSUES)
    assert_that(summary.issue_count).is_equal_to(1)
    assert_that(summary.duration).is_equal_to(1.25)
    assert_that(summary.to_dict()).does_not_contain_key("fixed_count")


def test_tool_summary_reports_passed_skipped_timed_out_and_errored() -> None:
    """Each ToolResult failure shape collapses to its own status."""
    results = [
        ToolResult(name="clean", success=True),
        ToolResult(name="absent", skipped=True, skip_reason="binary not installed"),
        ToolResult(name="slow", success=False, timed_out=True),
        ToolResult(name="broken", success=False, output="crashed"),
    ]

    summaries = tool_summaries_from_results(results=results, action=Action.CHECK)

    assert_that([summary.status for summary in summaries]).is_equal_to(
        [
            ToolRunStatus.PASSED,
            ToolRunStatus.SKIPPED,
            ToolRunStatus.TIMED_OUT,
            ToolRunStatus.ERRORED,
        ],
    )
    assert_that(summaries[1].to_dict()["skip_reason"]).is_equal_to(
        "binary not installed",
    )
    assert_that(summaries[0].duration).is_none()


def test_tool_summary_reports_fixed_count_for_fix_runs() -> None:
    """A fix run surfaces how many issues the tool resolved."""
    result = ToolResult(
        name="ruff",
        success=True,
        issues=[_issue()],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
    )

    summary = tool_summary_from_result(
        result=result,
        action=Action.FIX,
        scope=FindingScope.REMAINING,
    )

    assert_that(summary.status).is_equal_to(ToolRunStatus.PASSED)
    assert_that(summary.issue_count).is_equal_to(0)
    assert_that(summary.to_dict()["fixed_count"]).is_equal_to(1)
