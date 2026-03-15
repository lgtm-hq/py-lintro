"""Tests for the SARIF bridge reconstruction helpers."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.models import AISummary
from lintro.ai.output.sarif_bridge import (
    suggestions_from_results,
    summary_from_results,
)
from lintro.models.core.tool_result import ToolResult


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
