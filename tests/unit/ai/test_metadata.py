"""Tests for AI metadata helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.metadata import (
    AIFixSuggestionPayload,
    AIMetadataPayload,
    AISummaryPayload,
    attach_fix_suggestions_metadata,
    attach_fixed_count_metadata,
    attach_summary_metadata,
    attach_validation_counts_metadata,
    normalize_ai_metadata,
)
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.models.core.tool_result import ToolResult


def test_metadata_summary_and_fixes_coexist():
    result = ToolResult(name="ruff", success=True)
    summary = AISummary(overview="Overview")
    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
    )

    attach_summary_metadata(result, summary)
    attach_fix_suggestions_metadata(result, [suggestion])

    assert result.ai_metadata is not None
    assert_that(result.ai_metadata).contains_key("summary")
    assert_that(result.ai_metadata).contains_key("fix_suggestions")
    assert_that(result.ai_metadata["summary"]["overview"]).is_equal_to("Overview")
    assert_that(result.ai_metadata["fix_suggestions"]).is_length(1)


def test_metadata_normalize_supports_legacy_suggestions_key():
    raw = {
        "summary": {"overview": "Legacy summary"},
        "type": "fix_suggestions",
        "suggestions": [{"file": "a.py", "line": 1, "code": "E501"}],
    }

    normalized = normalize_ai_metadata(raw)

    assert_that(normalized).contains_key("summary")
    assert_that(normalized).contains_key("fix_suggestions")
    assert_that(normalized["fix_suggestions"]).is_length(1)


def test_metadata_fixed_count_is_attached_and_normalized():
    result = ToolResult(name="ruff", success=True)
    attach_fixed_count_metadata(result, 3)
    attach_validation_counts_metadata(
        result,
        verified_count=2,
        unverified_count=1,
    )

    assert result.ai_metadata is not None
    assert_that(result.ai_metadata).contains_key("fixed_count")
    assert_that(result.ai_metadata).contains_key("applied_count")
    assert_that(result.ai_metadata).contains_key("verified_count")
    assert_that(result.ai_metadata).contains_key("unverified_count")
    assert_that(result.ai_metadata["fixed_count"]).is_equal_to(3)
    assert_that(result.ai_metadata["applied_count"]).is_equal_to(3)
    assert_that(result.ai_metadata["verified_count"]).is_equal_to(2)
    assert_that(result.ai_metadata["unverified_count"]).is_equal_to(1)

    normalized = normalize_ai_metadata(result.ai_metadata or {})
    assert_that(normalized["fixed_count"]).is_equal_to(3)
    assert_that(normalized["applied_count"]).is_equal_to(3)
    assert_that(normalized["verified_count"]).is_equal_to(2)
    assert_that(normalized["unverified_count"]).is_equal_to(1)


def test_metadata_payload_to_dict_serialization():
    payload = AIMetadataPayload(
        summary=AISummaryPayload(overview="Test overview", estimated_effort="1h"),
        fix_suggestions=[
            AIFixSuggestionPayload(file="a.py", line=10, code="E501"),
        ],
        applied_count=1,
        verified_count=1,
        unverified_count=0,
        fixed_count=1,
    )
    d = payload.to_dict()

    assert_that(d).contains_key("summary")
    assert_that(d).contains_key("fix_suggestions")
    assert_that(d["summary"]["overview"]).is_equal_to("Test overview")
    assert_that(d["fix_suggestions"]).is_length(1)
    assert_that(d["fix_suggestions"][0]["file"]).is_equal_to("a.py")
    assert_that(d["applied_count"]).is_equal_to(1)
    assert_that(d["fixed_count"]).is_equal_to(1)
    assert_that(d["verified_count"]).is_equal_to(1)
    assert_that(d["unverified_count"]).is_equal_to(0)
