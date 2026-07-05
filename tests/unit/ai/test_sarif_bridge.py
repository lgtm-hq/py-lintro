"""Tests for the SARIF bridge reconstruction helpers."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.models import AISummary
from lintro.ai.output.sarif import StandardIssue, to_sarif
from lintro.ai.output.sarif_bridge import (
    standard_issues_from_results,
    suggestions_from_results,
    summary_from_results,
)
from lintro.enums.severity_level import SeverityLevel
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue


def test_suggestions_from_results_with_metadata() -> None:
    """Reconstruct AIFixSuggestion objects from ai_metadata dicts."""
    result = ToolResult(
        name="ruff",
        success=True,
        ai_metadata={
            "fix_suggestions": [
                {
                    "file": "src/main.py",
                    "line": 10,
                    "code": "B101",
                    "tool_name": "bandit",
                    "original_code": "assert x > 0",
                    "suggested_code": "if not x > 0:\n    raise ValueError",
                    "explanation": "Replace assert with if/raise",
                    "confidence": "high",
                    "risk_level": "safe-style",
                    "input_tokens": 150,
                    "output_tokens": 80,
                    "cost_estimate": 0.002,
                },
            ],
        },
    )

    suggestions = suggestions_from_results([result])

    assert_that(suggestions).is_length(1)
    s = suggestions[0]
    assert_that(s.file).is_equal_to("src/main.py")
    assert_that(s.line).is_equal_to(10)
    assert_that(s.code).is_equal_to("B101")
    assert_that(s.tool_name).is_equal_to("bandit")
    assert_that(s.explanation).is_equal_to("Replace assert with if/raise")
    assert_that(s.confidence).is_equal_to("high")
    assert_that(s.risk_level).is_equal_to("safe-style")
    assert_that(s.input_tokens).is_equal_to(150)
    assert_that(s.output_tokens).is_equal_to(80)
    assert_that(s.cost_estimate).is_equal_to(0.002)


def test_suggestions_from_results_no_metadata() -> None:
    """Return empty list when no AI metadata is present."""
    result = ToolResult(name="ruff", success=True)

    assert_that(suggestions_from_results([result])).is_empty()


def test_suggestions_from_results_empty_fix_suggestions() -> None:
    """Return empty list when fix_suggestions key is an empty list."""
    result = ToolResult(
        name="ruff",
        success=True,
        ai_metadata={"fix_suggestions": []},
    )

    assert_that(suggestions_from_results([result])).is_empty()


def test_summary_from_results_with_metadata() -> None:
    """Reconstruct AISummary from ai_metadata dict."""
    result = ToolResult(
        name="ruff",
        success=True,
        ai_metadata={
            "summary": {
                "overview": "Found 3 issues across 2 files.",
                "key_patterns": ["assert usage", "line length"],
                "priority_actions": ["Replace asserts"],
                "triage_suggestions": ["E501 may be intentional"],
                "estimated_effort": "30 minutes",
                "input_tokens": 500,
                "output_tokens": 200,
                "cost_estimate": 0.01,
            },
        },
    )

    summary = summary_from_results([result])

    assert_that(summary).is_not_none()
    assert summary is not None  # narrow type for mypy
    assert_that(summary).is_instance_of(AISummary)
    assert_that(summary.overview).is_equal_to("Found 3 issues across 2 files.")
    assert_that(summary.key_patterns).is_equal_to(["assert usage", "line length"])
    assert_that(summary.estimated_effort).is_equal_to("30 minutes")


def test_summary_from_results_no_metadata() -> None:
    """Return None when no summary metadata is present."""
    result = ToolResult(name="ruff", success=True)

    assert_that(summary_from_results([result])).is_none()


def test_summary_from_results_picks_first() -> None:
    """Return summary from the first result that has one."""
    result1 = ToolResult(
        name="ruff",
        success=True,
        ai_metadata={"summary": {"overview": "First summary"}},
    )
    result2 = ToolResult(
        name="mypy",
        success=True,
        ai_metadata={"summary": {"overview": "Second summary"}},
    )

    summary = summary_from_results([result1, result2])

    assert_that(summary).is_not_none()
    assert summary is not None  # narrow type for mypy
    assert_that(summary.overview).is_equal_to("First summary")


def test_sarif_format_end_to_end() -> None:
    """ToolResult with AI metadata produces valid SARIF JSON output."""
    result = ToolResult(
        name="ruff",
        success=True,
        ai_metadata={
            "fix_suggestions": [
                {
                    "file": "src/main.py",
                    "line": 10,
                    "code": "B101",
                    "tool_name": "bandit",
                    "original_code": "assert x > 0",
                    "suggested_code": "if not x > 0:\n    raise ValueError",
                    "explanation": "Replace assert with if/raise",
                    "confidence": "high",
                    "risk_level": "safe-style",
                },
            ],
            "summary": {
                "overview": "Found 1 issue.",
                "key_patterns": ["assert usage"],
                "priority_actions": ["Replace asserts"],
                "triage_suggestions": [],
                "estimated_effort": "5 minutes",
            },
        },
    )

    from lintro.ai.output.sarif import render_fixes_sarif

    suggestions = suggestions_from_results([result])
    summary = summary_from_results([result])
    sarif_json = render_fixes_sarif(suggestions, summary)

    sarif = json.loads(sarif_json)

    assert_that(sarif["version"]).is_equal_to("2.1.0")
    assert_that(sarif).contains_key("$schema")
    assert_that(sarif["runs"]).is_length(1)

    run = sarif["runs"][0]
    assert_that(run["tool"]["driver"]["name"]).is_equal_to("lintro-ai")
    assert_that(run["results"]).is_length(1)

    sarif_result = run["results"][0]
    assert_that(sarif_result["ruleId"]).is_equal_to("bandit/B101")
    assert_that(sarif_result["message"]["text"]).is_equal_to(
        "Replace assert with if/raise",
    )
    assert_that(
        sarif_result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
    ).is_equal_to("src/main.py")

    assert_that(run["properties"]["aiSummary"]["overview"]).is_equal_to(
        "Found 1 issue.",
    )


def test_standard_issues_from_results_extracts_fields() -> None:
    """Normalize BaseIssue objects from result.issues without AI metadata."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues=[
            RuffIssue(
                file="src/main.py",
                line=10,
                column=5,
                code="F401",
                message="imported but unused",
                doc_url="https://docs.astral.sh/ruff/rules/F401",
            ),
        ],
    )

    standard = standard_issues_from_results([result])

    assert_that(standard).is_length(1)
    issue = standard[0]
    assert_that(issue).is_instance_of(StandardIssue)
    assert_that(issue.tool_name).is_equal_to("ruff")
    assert_that(issue.file).is_equal_to("src/main.py")
    assert_that(issue.line).is_equal_to(10)
    assert_that(issue.column).is_equal_to(5)
    assert_that(issue.code).is_equal_to("F401")
    assert_that(issue.message).is_equal_to("imported but unused")
    assert_that(issue.doc_url).is_equal_to("https://docs.astral.sh/ruff/rules/F401")


def test_standard_issues_from_results_no_issues() -> None:
    """Return empty list when results carry no issues."""
    result = ToolResult(name="ruff", success=True, issues=[])

    assert_that(standard_issues_from_results([result])).is_empty()


def test_standard_sarif_without_ai_has_result_per_issue() -> None:
    """SARIF emits a standard result per issue with no AI metadata present."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues=[
            RuffIssue(
                file="a.py",
                line=3,
                column=1,
                code="E501",
                message="line too long",
                doc_url="https://docs.astral.sh/ruff/rules/E501",
            ),
            RuffIssue(
                file="b.py",
                line=7,
                column=0,
                code="F811",
                message="redefinition",
            ),
        ],
    )

    standard = standard_issues_from_results([result])
    sarif = to_sarif([], None, standard_issues=standard)

    assert_that(sarif["version"]).is_equal_to("2.1.0")
    assert_that(sarif).contains_key("$schema")
    assert_that(sarif["runs"]).is_length(1)

    run = sarif["runs"][0]
    results = run["results"]
    assert_that(results).is_length(2)

    first = results[0]
    assert_that(first["ruleId"]).is_equal_to("ruff/E501")
    assert_that(first["message"]["text"]).is_equal_to("line too long")
    location = first["locations"][0]["physicalLocation"]
    assert_that(location["artifactLocation"]["uri"]).is_equal_to("a.py")
    assert_that(location["region"]["startLine"]).is_equal_to(3)
    assert_that(location["region"]["startColumn"]).is_equal_to(1)

    second = results[1]
    assert_that(second["ruleId"]).is_equal_to("ruff/F811")
    assert_that(
        second["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
    ).is_equal_to("b.py")

    rules = run["tool"]["driver"]["rules"]
    rule_ids = [r["id"] for r in rules]
    assert_that(rule_ids).contains("ruff/E501", "ruff/F811")
    e501_rule = next(r for r in rules if r["id"] == "ruff/E501")
    assert_that(e501_rule["helpUri"]).is_equal_to(
        "https://docs.astral.sh/ruff/rules/E501",
    )


def test_standard_and_ai_results_are_additive() -> None:
    """Standard issues and AI suggestions coexist in the same SARIF run."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues=[
            RuffIssue(file="a.py", line=1, code="F401", message="unused"),
        ],
        ai_metadata={
            "fix_suggestions": [
                {
                    "file": "a.py",
                    "line": 1,
                    "code": "F401",
                    "tool_name": "ruff",
                    "suggested_code": "",
                    "explanation": "Remove import",
                    "confidence": "high",
                    "risk_level": "safe-style",
                },
            ],
        },
    )

    suggestions = suggestions_from_results([result])
    standard = standard_issues_from_results([result])
    sarif = to_sarif(suggestions, None, standard_issues=standard)

    results = sarif["runs"][0]["results"]
    assert_that(results).is_length(2)
    messages = [r["message"]["text"] for r in results]
    assert_that(messages).contains("unused", "Remove import")


def test_standard_issue_severity_maps_to_sarif_level() -> None:
    """Severity levels map to SARIF error/warning/note levels."""
    issues = [
        StandardIssue(
            tool_name="t",
            file="x.py",
            line=1,
            code="A",
            message="err",
            severity=SeverityLevel.ERROR,
        ),
        StandardIssue(
            tool_name="t",
            file="x.py",
            line=2,
            code="B",
            message="warn",
            severity=SeverityLevel.WARNING,
        ),
        StandardIssue(
            tool_name="t",
            file="x.py",
            line=3,
            code="C",
            message="info",
            severity=SeverityLevel.INFO,
        ),
    ]

    sarif = to_sarif([], None, standard_issues=issues)
    levels = [r["level"] for r in sarif["runs"][0]["results"]]

    assert_that(levels).is_equal_to(["error", "warning", "note"])
