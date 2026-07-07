"""Tests for AI data models."""

from __future__ import annotations

import dataclasses

from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion, AIResult, AISummary


def test_models_fix_suggestion_defaults():
    """All AIFixSuggestion fields have expected defaults."""
    fix = AIFixSuggestion()
    assert_that(fix.file).is_equal_to("")
    assert_that(fix.line).is_equal_to(0)
    assert_that(fix.code).is_equal_to("")
    assert_that(fix.tool_name).is_equal_to("")
    assert_that(fix.original_code).is_equal_to("")
    assert_that(fix.suggested_code).is_equal_to("")
    assert_that(fix.diff).is_equal_to("")
    assert_that(fix.explanation).is_equal_to("")
    assert_that(fix.confidence).is_equal_to("medium")
    assert_that(fix.risk_level).is_equal_to("")
    assert_that(fix.input_tokens).is_equal_to(0)
    assert_that(fix.output_tokens).is_equal_to(0)
    assert_that(fix.cost_estimate).is_equal_to(0.0)


def test_models_summary_defaults():
    """All AISummary fields have expected defaults."""
    summary = AISummary()
    assert_that(summary.overview).is_equal_to("")
    assert_that(summary.key_patterns).is_equal_to([])
    assert_that(summary.priority_actions).is_equal_to([])
    assert_that(summary.triage_suggestions).is_equal_to([])
    assert_that(summary.estimated_effort).is_equal_to("")
    assert_that(summary.input_tokens).is_equal_to(0)
    assert_that(summary.output_tokens).is_equal_to(0)
    assert_that(summary.cost_estimate).is_equal_to(0.0)


def test_models_summary_list_fields_are_independent():
    """Default list fields are not shared between AISummary instances."""
    first = AISummary()
    second = AISummary()
    first.key_patterns.append("pattern")
    assert_that(second.key_patterns).is_empty()


def test_models_summary_serialization_roundtrip():
    """AISummary serializes to a dict preserving all field values."""
    summary = AISummary(
        overview="looks fine",
        key_patterns=["p1"],
        priority_actions=["a1", "a2"],
        estimated_effort="low",
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.002,
    )
    data = dataclasses.asdict(summary)
    assert_that(data["overview"]).is_equal_to("looks fine")
    assert_that(data["key_patterns"]).is_equal_to(["p1"])
    assert_that(data["priority_actions"]).is_equal_to(["a1", "a2"])
    assert_that(data["estimated_effort"]).is_equal_to("low")
    assert_that(data["cost_estimate"]).is_equal_to(0.002)


def test_models_result_defaults():
    """All AIResult fields have expected defaults."""
    result = AIResult()
    assert_that(result.fixes_applied).is_equal_to(0)
    assert_that(result.fixes_failed).is_equal_to(0)
    assert_that(result.unfixed_issues).is_equal_to(0)
    assert_that(result.budget_exceeded).is_false()
    assert_that(result.error).is_false()
    assert_that(result.message).is_equal_to("")


def test_models_result_field_assignment_and_serialization():
    """AIResult stores provided values and serializes them to a dict."""
    result = AIResult(
        fixes_applied=3,
        fixes_failed=1,
        unfixed_issues=2,
        budget_exceeded=True,
        error=True,
        message="budget exceeded",
    )
    data = dataclasses.asdict(result)
    assert_that(data["fixes_applied"]).is_equal_to(3)
    assert_that(data["fixes_failed"]).is_equal_to(1)
    assert_that(data["unfixed_issues"]).is_equal_to(2)
    assert_that(data["budget_exceeded"]).is_true()
    assert_that(data["error"]).is_true()
    assert_that(data["message"]).is_equal_to("budget exceeded")
