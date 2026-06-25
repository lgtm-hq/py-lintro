"""Tests for review orchestrator."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.orchestrator import (
    parse_review_response,
    run_review,
    strip_json_fences,
)


def _sample_response_json(*, include_finding: bool = True) -> str:
    finding = (
        {
            "severity": "P1",
            "category": "security",
            "file": "src/main.py",
            "line": 12,
            "title": "Fail-open default",
            "description": "Unknown status grants access",
            "cause": "else branch returns Active",
            "fix": "Default to Expired",
            "confidence": "high",
            "checklist_ids": [1],
        }
        if include_finding
        else None
    )
    payload = {
        "summary": "Merge with fixes.",
        "checklist": [
            {"id": 1, "answer": "yes", "evidence": "src/main.py:12"},
        ],
        "findings": [finding] if finding is not None else [],
    }
    return json.dumps(payload)


def _mock_provider(*, content: str) -> MagicMock:
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    provider.complete.return_value = AIResponse(
        content=content,
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )
    return provider


def test_strip_json_fences_removes_markdown_wrapper() -> None:
    """Fence stripper extracts JSON from markdown code blocks."""
    content = '```json\n{"summary": "ok"}\n```'
    stripped = strip_json_fences(content=content)

    assert_that(stripped).is_equal_to('{"summary": "ok"}')


def test_parse_review_response_validates_required_keys() -> None:
    """Parser accepts valid review JSON payloads."""
    payload = parse_review_response(content=_sample_response_json())

    assert_that(payload["summary"]).contains("Merge")
    assert_that(payload["checklist"]).is_length(1)


def test_run_review_depth1_returns_review_result() -> None:
    """Depth 1 review produces findings from mocked provider response."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    checklist_items = [
        ChecklistItem(
            id=1,
            question="Example?",
            triggers=[],
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    provider = _mock_provider(content=_sample_response_json())

    with patch(
        "lintro.ai.review.orchestrator.complete_with_fallback",
        side_effect=lambda _provider, _prompt, **kwargs: _provider.complete(
            _prompt,
            system=kwargs.get("system"),
            max_tokens=kwargs.get("max_tokens", 1024),
            timeout=kwargs.get("timeout", 60.0),
        ),
    ):
        result = run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True),
            depth=1,
            checklist_items=checklist_items,
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(result.summary).contains("Merge")
    assert_that(result.findings).is_not_empty()
    assert_that(result.has_p1_findings).is_true()


def test_run_review_empty_diff_returns_empty_result() -> None:
    """Empty diff returns graceful empty review result."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[],
        unified_diff="",
        pr_metadata=None,
    )
    provider = _mock_provider(content="{}")

    result = run_review(
        context,
        provider=provider,
        ai_config=AIConfig(enabled=True),
        depth=1,
        checklist_items=[],
        checklist_text="",
        classifications=[],
    )

    assert_that(result.summary).contains("No changes")
    assert_that(result.findings).is_empty()


def test_run_review_depth2_calls_provider_twice() -> None:
    """Depth 2 runs question generation before the main review pass."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff --git a/src/main.py b/src/main.py\n+change",
        pr_metadata=None,
    )
    provider = _mock_provider(
        content='{"generated_questions": [{"id": "G1", "question": "Extra?"}]}',
    )
    provider.complete.side_effect = [
        AIResponse(
            content='{"generated_questions": [{"id": "G1", "question": "Extra?"}]}',
            model="claude-sonnet-4-20250514",
            input_tokens=50,
            output_tokens=20,
            cost_estimate=0.005,
            provider="anthropic",
        ),
        AIResponse(
            content=_sample_response_json(include_finding=False),
            model="claude-sonnet-4-20250514",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.01,
            provider="anthropic",
        ),
    ]

    with patch(
        "lintro.ai.review.orchestrator.complete_with_fallback",
        side_effect=lambda _provider, _prompt, **kwargs: _provider.complete(
            _prompt,
            system=kwargs.get("system"),
            max_tokens=kwargs.get("max_tokens", 1024),
            timeout=kwargs.get("timeout", 60.0),
        ),
    ):
        run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True),
            depth=2,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(provider.complete.call_count).is_equal_to(2)
