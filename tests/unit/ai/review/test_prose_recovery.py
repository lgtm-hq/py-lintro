"""Review-level recovery of prose (non-JSON) model answers (#1853).

A prose answer used to abort the chunk with ``kind=invalid_response`` and throw
away findings the model had already produced. These tests pin the recovery
ladder: parse -> one schema-reminder retry -> unstructured findings, with the
complete answer preserved at every step.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AICostBudgetExceededError, AIProviderError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.response_recovery import (
    SCHEMA_RETRY_MIN_TIMEOUT,
    UNSTRUCTURED_CATEGORY,
)

_PROSE = (
    "Reviewed the four commits. Two actionable findings:\n\n"
    "1. The pending-announcement drop is incomplete — the observer re-arms "
    "after close (SearchDropdown.astro:258)\n\n"
    "2. The Tab wrap skips the close button.\n"
) + ("tail " * 400)


def _valid_payload() -> str:
    """Return a schema-conforming review response.

    Returns:
        JSON text with the required review keys.
    """
    return json.dumps(
        {
            "summary": "Merge with fixes.",
            "checklist": [{"id": 1, "answer": "yes", "evidence": "src/main.py:1"}],
            "findings": [
                {
                    "severity": "P1",
                    "category": "logic-bug",
                    "file": "src/main.py",
                    "line": 12,
                    "title": "Observer re-arms after close",
                    "description": "The MutationObserver is never disconnected",
                    "cause": "No stored reference",
                    "fix": "Disconnect in closeSearch()",
                    "confidence": "high",
                    "checklist_ids": [1],
                },
            ],
        },
    )


def _provider() -> MagicMock:
    """Return a mock provider with sessions disabled.

    Returns:
        A provider double suitable for single-chunk reviews.
    """
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    return provider


def _context() -> ReviewContext:
    """Return a single-file review context.

    Returns:
        A review context covering ``src/main.py``.
    """
    return ReviewContext(
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


def _run(
    *,
    responses: list[AIResponse],
    api_timeout: float = 900.0,
) -> tuple[ReviewResult, list[dict[str, object]]]:
    """Run a single-chunk review against a scripted sequence of responses.

    Args:
        responses: Responses returned by successive ``call_ai`` calls.
        api_timeout: Per-call timeout budget for the run.

    Returns:
        Tuple of the review result and the recorded ``call_ai`` kwargs.
    """
    calls: list[dict[str, object]] = []
    queue = list(responses)

    async def _call_ai(**kwargs: object) -> AIResponse:
        calls.append(kwargs)
        return queue.pop(0)

    with patch("lintro.ai.review.orchestrator.call_ai", side_effect=_call_ai):
        result = run_review(
            _context(),
            provider=_provider(),
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                api_timeout=api_timeout,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )
    return result, calls


def _response(content: str, *, cost: float = 0.01) -> AIResponse:
    """Return an ``AIResponse`` carrying *content*.

    Args:
        content: Response text.
        cost: Estimated cost for the call.

    Returns:
        The constructed response.
    """
    return AIResponse(
        content=content,
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=cost,
        provider="anthropic",
    )


@pytest.fixture(autouse=True)
def _isolate_captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep raw-response captures out of the working tree.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)


def test_json_happy_path_makes_no_retry() -> None:
    """A schema-conforming answer is parsed with exactly one provider call."""
    result, calls = _run(responses=[_response(_valid_payload())])

    assert_that(calls).is_length(1)
    assert_that(result.findings).is_length(1)
    assert_that(result.findings[0].title).is_equal_to("Observer re-arms after close")


def test_prose_triggers_one_schema_reminder_retry() -> None:
    """A prose answer is retried once, and the retry's JSON is used."""
    result, calls = _run(
        responses=[_response(_PROSE), _response(_valid_payload())],
    )

    assert_that(calls).is_length(2)
    assert_that(str(calls[1]["user_prompt"])).contains("Do not repeat the review")
    assert_that(result.findings).is_length(1)
    assert_that(result.findings[0].category).is_not_equal_to(UNSTRUCTURED_CATEGORY)


def test_retry_receives_the_remaining_timeout_budget() -> None:
    """The reminder call is bounded so it cannot double the timeout budget."""
    _, calls = _run(
        responses=[_response(_PROSE), _response(_valid_payload())],
        api_timeout=900.0,
    )

    assert_that(calls[1]["timeout"]).is_instance_of(float)
    assert_that(calls[1]["timeout"]).is_less_than_or_equal_to(450.0)
    assert_that(calls[1]["timeout"]).is_greater_than_or_equal_to(
        SCHEMA_RETRY_MIN_TIMEOUT,
    )


def test_prose_twice_falls_back_to_unstructured_findings() -> None:
    """Exactly one retry, then the prose is surfaced instead of discarded."""
    result, calls = _run(
        responses=[_response(_PROSE), _response("Still prose, sorry.")],
    )

    assert_that(calls).is_length(2)
    assert_that(result.findings).is_length(1)
    assert_that(result.findings[0].category).is_equal_to(UNSTRUCTURED_CATEGORY)
    assert_that(result.findings[0].description).is_equal_to("Still prose, sorry.")


def test_unstructured_fallback_preserves_the_full_answer() -> None:
    """No part of the model's answer is truncated on the fallback path."""
    result, _ = _run(responses=[_response(_PROSE), _response(_PROSE)])

    assert_that(result.findings[0].description).is_equal_to(_PROSE.strip())
    assert_that(result.summary).contains(_PROSE.strip())


def test_short_timeout_budget_skips_the_retry() -> None:
    """When no retry fits the budget, the prose is recovered immediately."""
    result, calls = _run(
        responses=[_response(_PROSE)],
        api_timeout=SCHEMA_RETRY_MIN_TIMEOUT,
    )

    assert_that(calls).is_length(1)
    assert_that(result.findings).is_length(1)
    assert_that(result.findings[0].category).is_equal_to(UNSTRUCTURED_CATEGORY)
    assert_that(result.findings[0].description).is_equal_to(_PROSE.strip())


def test_failed_retry_still_recovers_the_original_answer() -> None:
    """A retry that errors is never worse than not retrying at all."""
    queue: list[AIResponse | Exception] = [
        _response(_PROSE),
        AIProviderError("provider exploded"),
    ]
    calls: list[dict[str, object]] = []

    async def _call_ai(**kwargs: object) -> AIResponse:
        calls.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("lintro.ai.review.orchestrator.call_ai", side_effect=_call_ai):
        result = run_review(
            _context(),
            provider=_provider(),
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                api_timeout=900.0,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(calls).is_length(2)
    assert_that(result.findings[0].category).is_equal_to(UNSTRUCTURED_CATEGORY)
    assert_that(result.findings[0].description).is_equal_to(_PROSE.strip())


def test_cost_cap_on_the_retry_is_not_swallowed() -> None:
    """The budget stop must propagate, not be recovered as prose."""
    queue: list[AIResponse | Exception] = [
        _response(_PROSE),
        AICostBudgetExceededError("cost cap reached"),
    ]

    async def _call_ai(**_kwargs: object) -> AIResponse:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("lintro.ai.review.orchestrator.call_ai", side_effect=_call_ai):
        result = run_review(
            _context(),
            provider=_provider(),
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                api_timeout=900.0,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    # The orchestrator finalizes a partial review on the cost cap rather than
    # continuing; the prose fallback must not have masked the stop.
    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).is_not_empty()


def test_retry_usage_is_charged_to_the_chunk() -> None:
    """Both calls' tokens and cost are attributed, not just the last one."""
    result, _ = _run(
        responses=[
            _response(_PROSE, cost=0.01),
            _response(_valid_payload(), cost=0.02),
        ],
    )

    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(200)
    assert_that(result.metadata.token_usage["completion"]).is_equal_to(100)
    assert_that(result.metadata.cost_estimate_usd).is_close_to(0.03, 1e-9)
