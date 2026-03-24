"""Tests for JSON output AI metadata serialization."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import create_json_output


def test_create_json_output_includes_summary_and_fix_suggestions_together() -> None:
    """Verify JSON output includes both AI summary and fix suggestions."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "summary": {
            "overview": "Summary text",
            "key_patterns": [],
            "priority_actions": [],
            "triage_suggestions": [],
            "estimated_effort": "10 minutes",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_estimate": 0.001,
        },
        "fix_suggestions": [
            {
                "file": "src/main.py",
                "line": 1,
                "code": "B101",
                "explanation": "Replace assert",
                "confidence": "high",
                "diff": "--- a/src/main.py",
                "input_tokens": 5,
                "output_tokens": 4,
                "cost_estimate": 0.001,
            },
        ],
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    assert_that(data).contains_key("ai_summary")
    assert_that(data["ai_summary"]["overview"]).is_equal_to("Summary text")
    assert_that(data["results"][0]["ai_metadata"]).contains_key("summary")
    assert_that(data["results"][0]["ai_metadata"]).contains_key(
        "fix_suggestions",
    )


def test_create_json_output_normalizes_legacy_suggestions_key() -> None:
    """Test that legacy suggestions key is normalized correctly."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "summary": {"overview": "Legacy"},
        "suggestions": [{"file": "a.py", "line": 1}],
        "type": "fix_suggestions",
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    assert_that(data["results"][0]["ai_metadata"]).contains_key(
        "fix_suggestions",
    )
    assert_that(data["results"][0]["ai_metadata"]).does_not_contain_key(
        "suggestions",
    )


def test_create_json_output_includes_ai_count_fields() -> None:
    """Verify AI count fields are serialized into JSON output."""
    result = ToolResult(name="ruff", success=False, issues_count=3)
    result.ai_metadata = {
        "fixed_count": 2,
        "verified_count": 1,
        "unverified_count": 1,
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=3,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    ai_meta = data["results"][0]["ai_metadata"]
    assert_that(ai_meta["fixed_count"]).is_equal_to(2)
    assert_that(ai_meta["applied_count"]).is_equal_to(2)
    assert_that(ai_meta["verified_count"]).is_equal_to(1)
    assert_that(ai_meta["unverified_count"]).is_equal_to(1)


def test_create_json_output_includes_ai_metrics() -> None:
    """Verify AI telemetry metrics are preserved through normalization."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "ai_metrics": {
            "total_api_calls": 5,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "total_cost_usd": 0.01,
        },
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    ai_meta = data["results"][0]["ai_metadata"]
    assert_that(ai_meta).contains_key("ai_metrics")
    assert_that(ai_meta["ai_metrics"]["total_api_calls"]).is_equal_to(5)
    assert_that(ai_meta["ai_metrics"]["total_cost_usd"]).is_equal_to(0.01)


def test_create_json_output_counts_survive_legacy_normalization() -> None:
    """Verify counts and metrics survive alongside legacy key normalization."""
    result = ToolResult(name="ruff", success=False, issues_count=1)
    result.ai_metadata = {
        "summary": {"overview": "Legacy with counts"},
        "suggestions": [{"file": "a.py", "line": 1}],
        "type": "fix_suggestions",
        "fixed_count": 1,
        "verified_count": 1,
        "unverified_count": 0,
        "ai_metrics": {
            "total_api_calls": 2,
            "total_cost_usd": 0.005,
        },
    }

    data = create_json_output(
        action=Action.CHECK,
        results=[result],
        total_issues=1,
        total_fixed=0,
        total_remaining=0,
        exit_code=1,
    )

    ai_meta = data["results"][0]["ai_metadata"]
    assert_that(ai_meta).contains_key("fix_suggestions")
    assert_that(ai_meta).does_not_contain_key("suggestions")
    assert_that(ai_meta["fixed_count"]).is_equal_to(1)
    assert_that(ai_meta["verified_count"]).is_equal_to(1)
    assert_that(ai_meta["unverified_count"]).is_equal_to(0)
    assert_that(ai_meta).contains_key("ai_metrics")
    assert_that(ai_meta["ai_metrics"]["total_api_calls"]).is_equal_to(2)
