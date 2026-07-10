"""Tests for review orchestrator."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIError
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import (
    _review_chunk,
    build_git_native_review_prompt,
    parse_review_response,
    resolve_review_chunks,
    run_review,
    strip_json_fences,
)
from lintro.ai.review.progress import ReviewProgressCallback


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


def _one_file_context() -> ReviewContext:
    """Build a single-file review context."""
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


def test_run_review_marks_cli_transport_tokens_estimated() -> None:
    """CLI transport flags token usage as locally estimated in metadata."""
    provider = _mock_provider(content=_sample_response_json())

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: provider.complete(
            user_prompt,
            system=system_prompt,
            max_tokens=kwargs.get("max_tokens", 1024),
        ),
    ):
        result = run_review(
            _one_file_context(),
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(result.metadata.token_usage_estimated).is_true()
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(
        result.metadata.chunks_total,
    )


def test_run_review_returns_partial_on_cost_cap() -> None:
    """Cost cap mid-run finalizes a partial review instead of erroring."""
    provider = _mock_provider(content=_sample_response_json())
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]

    def _recording_call_ai(
        *,
        provider,
        user_prompt,
        budget=None,
        **kwargs,
    ):  # noqa: ANN001, ANN003, ANN202
        response = provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with (
        patch(
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.orchestrator.call_ai",
            side_effect=_recording_call_ai,
        ),
    ):
        result = run_review(
            _one_file_context(),
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                max_cost_usd=0.01,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).is_equal_to("cost cap ($0.01) reached")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that(result.metadata.chunks_total).is_equal_to(2)
    assert_that(result.findings).is_not_empty()


def test_run_review_partial_when_cost_cap_before_any_chunk() -> None:
    """Cap tripping before any chunk completes returns an actionable partial.

    A depth-2 chunk overspends the cap on its question-generation call, so the
    main review budget check raises before the chunk produces a partial. The
    result must be a clean, empty partial (``partial=True``, zero chunks
    reviewed) rather than the generic abort error.
    """
    provider = _mock_provider(content=_sample_response_json())

    def _recording_call_ai(
        *,
        provider,
        budget=None,
        **kwargs,
    ):  # noqa: ANN001, ANN003, ANN202
        response = provider.complete(
            kwargs.get("user_prompt", ""),
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        side_effect=_recording_call_ai,
    ):
        result = run_review(
            _one_file_context(),
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                max_cost_usd=0.005,
            ),
            depth=2,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(0)
    assert_that(result.metadata.stopped_reason).contains("cost cap")
    assert_that(result.findings).is_empty()


def test_run_review_raises_on_genuine_provider_error_mid_review() -> None:
    """A real provider error mid-review still raises, never a silent partial."""
    provider = _mock_provider(content=_sample_response_json())
    chunks = [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]
    seen: list[str] = []

    def _flaky_call_ai(
        *,
        provider,
        budget=None,
        **kwargs,
    ):  # noqa: ANN001, ANN003, ANN202
        del budget
        seen.append("call")
        if len(seen) >= 2:
            raise AIError("anthropic: overloaded_error")
        return provider.complete(
            kwargs.get("user_prompt", ""),
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )

    with (
        patch(
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.orchestrator.call_ai",
            side_effect=_flaky_call_ai,
        ),
        pytest.raises(AIError),
    ):
        run_review(
            _one_file_context(),
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )


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
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    provider = _mock_provider(content=_sample_response_json())

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: provider.complete(
            user_prompt,
            system=system_prompt,
            max_tokens=kwargs.get("max_tokens", 1024),
        ),
    ):
        result = run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
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
        ai_config=AIConfig(enabled=True, transport=AITransport.API),
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
        "lintro.ai.review.orchestrator.call_ai",
        side_effect=lambda *, provider, user_prompt, system_prompt=None, **kwargs: provider.complete(
            user_prompt,
            system=system_prompt,
            max_tokens=kwargs.get("max_tokens", 1024),
        ),
    ):
        result = run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=2,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(provider.complete.call_count).is_equal_to(2)
    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(150)
    assert_that(result.metadata.token_usage["completion"]).is_equal_to(70)
    assert_that(result.metadata.cost_estimate_usd).is_equal_to(0.015)


def _single_chunk() -> ReviewChunk:
    """Build a one-file review chunk for direct ``_review_chunk`` tests."""
    return ReviewChunk(
        id=1,
        files=["src/main.py"],
        diff="diff --git a/src/main.py b/src/main.py\n+change",
        relationship=REL_SINGLE_FILE,
    )


def _single_file_context() -> ReviewContext:
    """Build a minimal review context for direct ``_review_chunk`` tests."""
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


def test_review_chunk_checks_budget_before_each_provider_call() -> None:
    """Depth-3 review checks the budget before every intra-chunk call."""
    events: list[str] = []
    budget = CostBudget(max_cost_usd=None)
    original_check = budget.check

    def _record_check() -> None:
        events.append("check")
        original_check()

    def _fake_call_ai(*, budget: CostBudget, **kwargs: object) -> AIResponse:
        del budget, kwargs
        events.append("call")
        return AIResponse(
            content=_sample_response_json(include_finding=False),
            model="auto",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.01,
            provider="cursor",
        )

    with (
        patch.object(budget, "check", side_effect=_record_check),
        patch(
            "lintro.ai.review.orchestrator.call_ai",
            side_effect=_fake_call_ai,
        ),
    ):
        _review_chunk(
            chunk=_single_chunk(),
            context=_single_file_context(),
            provider=MagicMock(),
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=3,
            checklist_text="1. [logic-bug] Example?",
            checklist_count=1,
            next_generated_checklist_id=100,
            classifications=[],
            lint_results=None,
            budget=budget,
        )

    # Three provider calls (extra checklist, main review, adversarial), each
    # preceded by a budget check.
    assert_that(events.count("call")).is_equal_to(3)
    for index, event in enumerate(events):
        if event == "call":
            assert_that(events[index - 1]).is_equal_to("check")


def test_review_chunk_budget_stops_runaway_calls() -> None:
    """An exhausted budget halts the chunk before overspending on more calls."""
    calls: list[str] = []
    budget = CostBudget(max_cost_usd=0.01)

    def _fake_call_ai(*, budget: CostBudget, **kwargs: object) -> AIResponse:
        del kwargs
        calls.append("call")
        response = AIResponse(
            content=_sample_response_json(include_finding=False),
            model="auto",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.02,
            provider="cursor",
        )
        budget.record(response.cost_estimate)
        return response

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        side_effect=_fake_call_ai,
    ):
        with pytest.raises(AIError):
            _review_chunk(
                chunk=_single_chunk(),
                context=_single_file_context(),
                provider=MagicMock(),
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                depth=3,
                checklist_text="1. [logic-bug] Example?",
                checklist_count=1,
                next_generated_checklist_id=100,
                classifications=[],
                lint_results=None,
                budget=budget,
            )

    # The first depth-2 call overspends the $0.01 cap; the budget check gates
    # the next call before a runaway depth-3 sweep can fire.
    assert_that(len(calls)).is_less_than(3)


def test_resolve_review_chunks_uses_fast_path_for_small_diff(
    sample_review_context: ReviewContext,
) -> None:
    """Small diffs within budget collapse to a single chunk."""
    chunks = resolve_review_chunks(
        context=sample_review_context,
        diff_budget=10_000,
        classifications=[],
    )

    assert_that(chunks).is_length(1)
    assert_that(chunks[0].files).is_length(5)
    assert_that(chunks[0].relationship).is_equal_to("directory-prefix")


def test_resolve_review_chunks_semantic_when_over_budget(
    sample_review_context: ReviewContext,
) -> None:
    """Oversized diffs still use semantic chunking."""
    chunks = resolve_review_chunks(
        context=sample_review_context,
        diff_budget=50,
        classifications=[],
    )

    assert_that(chunks).is_not_empty()
    assert_that(len(chunks)).is_greater_than(1)


def test_resolve_review_chunks_skips_fast_path_when_forced(
    sample_review_context: ReviewContext,
) -> None:
    """Thorough strictness can force semantic chunking even for small diffs."""
    chunks = resolve_review_chunks(
        context=sample_review_context,
        diff_budget=10_000,
        classifications=[],
        force_semantic_chunking=True,
    )

    assert_that(len(chunks)).is_greater_than(1)


def test_run_review_parallelizes_multiple_chunks(tmp_path: Path) -> None:
    """Multiple chunks run concurrently up to max_parallel_calls."""
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path=f"src/file{index}.py",
                status="modified",
                additions=1,
                deletions=0,
            )
            for index in range(4)
        ],
        unified_diff="diff",
        pr_metadata=None,
        repo_root=str(tmp_path),
    )
    chunks = [
        ReviewChunk(
            id=index + 1,
            files=[f"src/file{index}.py"],
            diff=f"+line{index}",
            relationship="single-file",
        )
        for index in range(4)
    ]
    provider = _mock_provider(content=_sample_response_json(include_finding=False))
    lock = threading.Lock()
    active = 0
    max_active = 0

    def _track_concurrency(*, provider, user_prompt, **kwargs):
        del user_prompt, kwargs
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return provider.complete("prompt")

    with (
        patch(
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=chunks,
        ),
        patch(
            "lintro.ai.review.orchestrator.call_ai",
            side_effect=_track_concurrency,
        ),
    ):
        run_review(
            context,
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                transport=AITransport.API,
                max_parallel_calls=4,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
        )

    assert_that(max_active).is_greater_than(1)
    assert_that(provider.complete.call_count).is_equal_to(4)


def test_run_review_aborts_progress_when_chunk_review_fails() -> None:
    """Progress tracker receives on_abort when a chunk review raises."""
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
    provider = _mock_provider(content=_sample_response_json())
    progress = MagicMock(spec=ReviewProgressCallback)

    with (
        patch(
            "lintro.ai.review.orchestrator.call_ai",
            side_effect=RuntimeError("provider failed"),
        ),
        pytest.raises(ReviewExecutionError),
    ):
        run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
            progress=progress,
        )

    progress.on_start.assert_called_once()
    progress.on_error.assert_called_once()
    progress.on_abort.assert_called_once()
    progress.on_complete.assert_not_called()


def test_run_review_propagates_chunk_error_when_progress_abort_raises() -> None:
    """Progress cleanup errors must not mask the original chunk review failure."""
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
    provider = _mock_provider(content=_sample_response_json())
    progress = MagicMock(spec=ReviewProgressCallback)
    progress.on_abort.side_effect = BrokenPipeError()

    with (
        patch(
            "lintro.ai.review.orchestrator.call_ai",
            side_effect=RuntimeError("provider failed"),
        ),
        pytest.raises(ReviewExecutionError) as exc_info,
    ):
        run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=[],
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
            progress=progress,
        )

    assert_that(exc_info.value.cause_message).contains("provider failed")
    progress.on_start.assert_called_once()
    progress.on_abort.assert_called_once()
    progress.on_complete.assert_not_called()


def test_run_review_returns_result_when_progress_complete_raises() -> None:
    """Progress cleanup errors must not discard a successful review result."""
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
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    provider = _mock_provider(content=_sample_response_json())
    progress = MagicMock(spec=ReviewProgressCallback)
    progress.on_complete.side_effect = BrokenPipeError()

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        side_effect=lambda *, provider, user_prompt, **kwargs: provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
            timeout=kwargs.get("timeout", 60.0),
        ),
    ):
        result = run_review(
            context,
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=checklist_items,
            checklist_text="1. [logic-bug] Example?",
            classifications=[],
            progress=progress,
        )

    assert_that(result.summary).contains("Merge")
    progress.on_complete.assert_called_once_with(total_findings=1)


def test_run_review_uses_git_native_prompt_for_cli_transport() -> None:
    """CLI transport uses git-native prompts for non-Cursor providers."""
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
    provider = _mock_provider(content=_sample_response_json())
    provider.name = "anthropic"

    with patch(
        "lintro.ai.review.orchestrator.build_git_native_review_prompt",
    ) as mock_git_native:
        mock_git_native.return_value = ("system", "user")
        with patch(
            "lintro.ai.review.orchestrator.call_ai",
            return_value=provider.complete("prompt"),
        ):
            run_review(
                context,
                provider=provider,
                ai_config=AIConfig(enabled=True, transport=AITransport.CLI),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
            )

    mock_git_native.assert_called_once()


def test_build_git_native_review_prompt_embeds_diff_when_requested(
    sample_review_context: ReviewContext,
) -> None:
    """Git-native prompts can inline the diff for budget-fitting chunks."""
    chunk = ReviewChunk(
        id=1,
        files=["src/lib/math.py"],
        diff="diff --git a/src/lib/math.py b/src/lib/math.py\n+1\n",
        relationship="single-file",
        metadata_note=None,
    )

    _, user_prompt = build_git_native_review_prompt(
        chunk=chunk,
        context=sample_review_context,
        checklist_text="1. [logic-bug] Example?",
        checklist_count=1,
        interaction_paths="(none)",
        embed_diff=True,
    )

    assert_that(user_prompt).contains("<pull_request_diff>")
    assert_that(user_prompt).contains("src/lib/math.py")
    assert_that(user_prompt).does_not_contain("git diff")


def test_build_git_native_review_prompt_uses_git_command_when_not_embedded(
    sample_review_context: ReviewContext,
) -> None:
    """Large diffs keep agentic git diff instructions under the opt-out.

    The delegated ``git diff`` command bypasses secret redaction, so it is
    only emitted when the caller explicitly opts out of the redaction
    guarantee via ``allow_unredacted_git_native``.
    """
    chunk = ReviewChunk(
        id=1,
        files=["src/lib/math.py"],
        diff="diff --git a/src/lib/math.py b/src/lib/math.py\n+1\n",
        relationship="single-file",
        metadata_note=None,
    )

    _, user_prompt = build_git_native_review_prompt(
        chunk=chunk,
        context=sample_review_context,
        checklist_text="1. [logic-bug] Example?",
        checklist_count=1,
        interaction_paths="(none)",
        embed_diff=False,
        allow_unredacted_git_native=True,
    )

    assert_that(user_prompt).contains("git diff")
    assert_that(user_prompt).does_not_contain("<pull_request_diff>")
