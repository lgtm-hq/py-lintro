"""Tests for per-phase review timing instrumentation (issue #2148)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AICostBudgetExceededError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.github_render import format_run_mechanics
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_timings import ReviewTimings
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.timings import (
    ReviewPhase,
    ReviewTimingRecorder,
    format_duration,
    format_timing_summary,
)

_MODEL = "claude-sonnet-4-20250514"


def _timings_of(*, result: ReviewResult) -> ReviewTimings:
    """Return the result's timing breakdown, failing when it is absent.

    Args:
        result: Review result under test.

    Returns:
        The recorded timing breakdown.

    Raises:
        AssertionError: When the run recorded no timings.
    """
    timings = result.metadata.timings
    if timings is None:
        raise AssertionError("review metadata carried no timings")
    return timings


def _response_json() -> str:
    """Return a minimal valid review payload.

    Returns:
        JSON text the review parser accepts.
    """
    return json.dumps(
        {
            "summary": "Looks fine.",
            "checklist": [{"id": 1, "answer": "yes", "evidence": "src/f.py:1"}],
            "findings": [],
        },
    )


def _provider() -> MagicMock:
    """Return a mock provider returning a canned review payload.

    Returns:
        Configured mock provider.
    """
    provider = MagicMock()
    # The run session closes every provider it owns (#2302), so the
    # double has to model an awaitable ``aclose``.
    provider.aclose = AsyncMock()
    provider.model_name = _MODEL
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    provider.complete.return_value = AIResponse(
        content=_response_json(),
        model=_MODEL,
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )
    return provider


def _context(*, tmp_path: Path, count: int) -> ReviewContext:
    """Build a review context with ``count`` changed files.

    Args:
        tmp_path: Temporary repository root.
        count: Number of changed files.

    Returns:
        A populated review context.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path=f"src/file{index}.py",
                status="modified",
                additions=1,
                deletions=0,
            )
            for index in range(count)
        ],
        unified_diff="diff",
        pr_metadata=None,
        repo_root=str(tmp_path),
    )


def _chunks(*, count: int) -> list[ReviewChunk]:
    """Build ``count`` single-file chunks.

    Args:
        count: Number of chunks.

    Returns:
        Ordered single-file chunks.
    """
    return [
        ReviewChunk(
            id=index + 1,
            files=[f"src/file{index}.py"],
            diff=f"+line{index}",
            relationship="single-file",
        )
        for index in range(count)
    ]


def _run(
    *,
    tmp_path: Path,
    chunk_count: int,
    depth: int = 1,
    max_parallel_calls: int | None = None,
    call_delay: float = 0.0,
    stop_on_first_call: bool = False,
    stop_during_first_call: bool = False,
    max_cost_usd: float | None = None,
) -> ReviewResult:
    """Run a review with the provider call stubbed out.

    Args:
        tmp_path: Temporary repository root.
        chunk_count: Number of chunks to force.
        depth: Review depth.
        max_parallel_calls: Concurrency ceiling for chunk calls; ``None``
            keeps the production ``AIConfig`` default.
        call_delay: Seconds each stubbed provider call sleeps.
        stop_on_first_call: When True, the first provider call raises a
            cost-cap stop so the remaining queued chunks are cancelled.
        stop_during_first_call: When True, the injected SIGTERM stop event
            is set while the first provider call is sleeping.
        max_cost_usd: Optional spend cap; the orchestrator serializes chunk
            calls whenever one is set.

    Returns:
        The completed review result.
    """
    provider = _provider()
    stop = asyncio.Event()
    ai_config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_cost_usd=max_cost_usd,
    )
    if max_parallel_calls is not None:
        ai_config = ai_config.model_copy(
            update={"max_parallel_calls": max_parallel_calls},
        )

    async def _call(*, provider: MagicMock, **kwargs: Any) -> AIResponse:
        """Return the canned response after an optional delay.

        Args:
            provider: Mock provider under test.
            **kwargs: Ignored call arguments.

        Returns:
            The provider's canned response.

        Raises:
            AICostBudgetExceededError: When ``stop_on_first_call`` is set.
        """
        del kwargs
        if stop_during_first_call:
            stop.set()
        if call_delay:
            await asyncio.sleep(call_delay)
        if stop_on_first_call:
            raise AICostBudgetExceededError("cost cap reached")
        response: AIResponse = provider.complete("prompt")
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_chunks(count=chunk_count),
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_call),
    ):
        return run_review(
            _context(tmp_path=tmp_path, count=chunk_count),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=ai_config,
                depth=depth,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                context_collection_seconds=0.5,
                stop=stop,
            ),
        )


# ---------------------------------------------------------------------------
# Recorder and formatter
# ---------------------------------------------------------------------------


def test_recorder_keeps_phases_in_first_occurrence_order() -> None:
    """Phase spans read chronologically regardless of when they repeat."""
    recorder = ReviewTimingRecorder()
    recorder.add_phase(name=ReviewPhase.CONTEXT_COLLECTION, seconds=1.0)
    recorder.add_phase(name=ReviewPhase.PROVIDER, seconds=2.0)
    recorder.add_phase(name=ReviewPhase.CONTEXT_COLLECTION, seconds=3.0)

    timings = recorder.build(total_seconds=10.0, max_parallel=2)

    assert_that([span.name for span in timings.phases]).is_equal_to(
        ["context_collection", "provider"],
    )
    assert_that(timings.phase_seconds(name="context_collection")).is_equal_to(4.0)
    assert_that(timings.phases[0].occurrences).is_equal_to(2)


def test_recorder_clamps_negative_and_missing_values() -> None:
    """Negative spans clamp to zero and unknown phases report zero seconds."""
    recorder = ReviewTimingRecorder()
    recorder.add_phase(name=ReviewPhase.CHUNKING, seconds=-5.0)

    timings = recorder.build(total_seconds=-1.0)

    assert_that(timings.phase_seconds(name="chunking")).is_equal_to(0.0)
    assert_that(timings.phase_seconds(name="nope")).is_equal_to(0.0)
    assert_that(timings.total_seconds).is_equal_to(0.0)
    assert_that(timings.max_parallel).is_equal_to(1)


def test_recorder_phase_context_manager_records_on_exception() -> None:
    """A phase span is recorded even when the timed block raises."""
    recorder = ReviewTimingRecorder()

    with pytest.raises(RuntimeError), recorder.phase(name=ReviewPhase.PROVIDER):
        raise RuntimeError("boom")

    assert_that(recorder.build().phase_seconds(name="provider")).is_greater_than(0.0)


def test_recorder_orders_chunks_by_index() -> None:
    """Chunk detail is ordered by chunk index, not completion order."""
    recorder = ReviewTimingRecorder()
    for index in (2, 0, 1):
        recorder.add_chunk(
            chunk_index=index,
            files=1,
            queued_seconds=float(index),
            in_flight_seconds=1.0,
        )

    timings = recorder.build(total_seconds=5.0)

    assert_that([chunk.chunk_index for chunk in timings.chunks]).is_equal_to([0, 1, 2])
    assert_that(timings.chunks[2].total_seconds).is_equal_to(3.0)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0.0s"),
        (0.42, "0.4s"),
        (22.0, "22.0s"),
        (59.9, "59.9s"),
        (292.0, "4m52s"),
        (3723.0, "1h02m03s"),
        (-3.0, "0.0s"),
    ],
    ids=[
        "zero",
        "sub_second",
        "seconds",
        "just_under_a_minute",
        "minutes",
        "hours",
        "negative",
    ],
)
def test_format_duration_renders_compact_units(
    seconds: float,
    expected: str,
) -> None:
    """Durations render compactly across the sub-second to hour range.

    Args:
        seconds: Duration under test.
        expected: Expected rendering.
    """
    assert_that(format_duration(seconds=seconds)).is_equal_to(expected)


def test_format_timing_summary_matches_documented_shape() -> None:
    """The one-line summary leads with the dominant phase and chunk detail."""
    recorder = ReviewTimingRecorder()
    recorder.add_phase(name=ReviewPhase.CONTEXT_COLLECTION, seconds=22.0)
    recorder.add_phase(name=ReviewPhase.PROVIDER, seconds=250.0)
    recorder.add_phase(name=ReviewPhase.PARSE_MERGE, seconds=8.0)
    for index in range(7):
        recorder.add_chunk(
            chunk_index=index,
            files=1,
            queued_seconds=1.0,
            in_flight_seconds=30.0,
        )

    summary = format_timing_summary(
        timings=recorder.build(total_seconds=292.0, max_parallel=5),
    )

    assert_that(summary).is_equal_to(
        "total 4m52s — provider 4m10s (7 chunks, max parallel 5), "
        "context 22.0s, merge 8.0s",
    )


def test_format_timing_summary_nests_depth_phases_inside_provider() -> None:
    """Depth phases are listed inside the provider envelope, not as peers."""
    recorder = ReviewTimingRecorder()
    recorder.add_phase(name=ReviewPhase.CONTEXT_COLLECTION, seconds=2.0)
    recorder.add_phase(name=ReviewPhase.GENERATED_QUESTIONS, seconds=30.0)
    recorder.add_phase(name=ReviewPhase.PROVIDER, seconds=100.0)
    recorder.add_phase(name=ReviewPhase.ADVERSARIAL, seconds=12.0)
    recorder.add_chunk(
        chunk_index=0,
        files=1,
        queued_seconds=0.0,
        in_flight_seconds=100.0,
    )

    summary = format_timing_summary(
        timings=recorder.build(total_seconds=104.0, max_parallel=1),
    )

    assert_that(summary).is_equal_to(
        "total 1m44s — provider 1m40s (1 chunk, max parallel 1, "
        "questions 30.0s, adversarial 12.0s), context 2.0s",
    )


def test_format_timing_summary_singularizes_one_chunk() -> None:
    """A single-chunk run reads ``1 chunk``, not ``1 chunks``."""
    recorder = ReviewTimingRecorder()
    recorder.add_phase(name=ReviewPhase.PROVIDER, seconds=5.0)
    recorder.add_chunk(
        chunk_index=0,
        files=1,
        queued_seconds=0.0,
        in_flight_seconds=5.0,
    )

    summary = format_timing_summary(
        timings=recorder.build(total_seconds=6.0, max_parallel=1),
    )

    assert_that(summary).contains("(1 chunk, max parallel 1)")


def test_format_timing_summary_without_phases_reports_only_total() -> None:
    """A run with no recorded phase still renders a total."""
    summary = format_timing_summary(
        timings=ReviewTimingRecorder().build(total_seconds=3.0),
    )

    assert_that(summary).is_equal_to("total 3.0s")


def test_format_timing_summary_drops_zero_length_phases() -> None:
    """Phases that took no measurable time are omitted from the summary."""
    recorder = ReviewTimingRecorder()
    recorder.add_phase(name=ReviewPhase.PROVIDER, seconds=4.0)
    recorder.add_phase(name=ReviewPhase.VALIDATION, seconds=0.0)

    summary = format_timing_summary(
        timings=recorder.build(total_seconds=4.0, max_parallel=1),
    )

    assert_that(summary).does_not_contain("validation")


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


def test_run_review_exposes_ordered_spans_in_json(tmp_path: Path) -> None:
    """The JSON payload carries an ordered top-level ``timings`` block.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=2)

    payload = review_result_to_dict(result=result)
    timings = payload["timings"]
    names = [span["name"] for span in timings["phases"]]

    assert_that(payload["metadata"]).does_not_contain_key("timings")
    assert_that(names).contains(
        "context_collection",
        "chunking",
        "provider",
        "parse_merge",
        "validation",
    )
    assert_that(names.index("context_collection")).is_less_than(names.index("chunking"))
    assert_that(names.index("chunking")).is_less_than(names.index("provider"))
    assert_that(names.index("provider")).is_less_than(names.index("parse_merge"))
    assert_that(names.index("parse_merge")).is_less_than(names.index("validation"))
    assert_that(timings["total_seconds"]).is_greater_than_or_equal_to(0.0)
    assert_that(timings["chunks"]).is_length(2)
    assert_that(json.loads(json.dumps(payload))["timings"]).is_equal_to(timings)


def test_run_review_reports_context_collection_from_caller(tmp_path: Path) -> None:
    """The caller's context-collection seconds land in the phase spans.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=1)

    assert_that(
        _timings_of(result=result).phase_seconds(name="context_collection"),
    ).is_close_to(0.5, 0.001)
    # The legacy flat mapping stays byte-compatible for existing consumers.
    assert_that(set(result.metadata.phase_timings)).is_equal_to(
        {"context_collection", "provider", "parse_merge"},
    )


def test_parallel_chunks_report_queued_and_in_flight(tmp_path: Path) -> None:
    """Chunks held back by the semaphore report queued time; leaders do not.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(
        tmp_path=tmp_path,
        chunk_count=4,
        max_parallel_calls=1,
        call_delay=0.02,
    )

    timings = _timings_of(result=result)
    assert_that(timings.max_parallel).is_equal_to(1)
    assert_that(timings.chunks).is_length(4)
    for chunk in timings.chunks:
        assert_that(chunk.in_flight_seconds).is_greater_than_or_equal_to(0.02)
        assert_that(chunk.failed).is_false()
    # Serialized behind one slot, every chunk after the leader waits; the
    # leader (whichever index was admitted first) is admitted before it could
    # have spent a full call in flight.
    leader = min(timings.chunks, key=lambda chunk: chunk.queued_seconds)
    assert_that(leader.queued_seconds).is_less_than(leader.in_flight_seconds)
    assert_that(
        [chunk.queued_seconds for chunk in timings.chunks[1:]],
    ).is_not_empty()
    assert_that(
        max(chunk.queued_seconds for chunk in timings.chunks),
    ).is_greater_than_or_equal_to(0.02)


def test_unqueued_chunks_report_no_semaphore_wait(tmp_path: Path) -> None:
    """With slots for every chunk, queued time stays near zero.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(
        tmp_path=tmp_path,
        chunk_count=3,
        max_parallel_calls=3,
        call_delay=0.02,
    )

    timings = _timings_of(result=result)
    assert_that(timings.max_parallel).is_equal_to(3)
    # With a slot per chunk, no chunk waits anywhere near as long as it spent
    # in flight. A relative bound does not depend on scheduler latency.
    for chunk in timings.chunks:
        assert_that(chunk.queued_seconds).is_less_than(chunk.in_flight_seconds)


def test_single_chunk_run_records_one_chunk_without_queue(tmp_path: Path) -> None:
    """The single-chunk fast path still reports a chunk with zero queue time.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=1, call_delay=0.01)

    timings = _timings_of(result=result)
    assert_that(timings.chunks).is_length(1)
    assert_that(timings.chunks[0].queued_seconds).is_equal_to(0.0)
    assert_that(timings.chunks[0].in_flight_seconds).is_greater_than_or_equal_to(0.01)


def test_total_seconds_includes_context_collection(tmp_path: Path) -> None:
    """The run total is back-dated by the caller's context-collection time.

    The summary line reads ``total ... — provider ..., context ...``, so the
    total has to cover the context phase rather than starting after it.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=1)

    timings = _timings_of(result=result)
    assert_that(timings.total_seconds).is_greater_than_or_equal_to(0.5)
    assert_that(result.metadata.duration_seconds).is_close_to(
        timings.total_seconds,
        0.001,
    )


def test_chunks_cancelled_while_queued_still_report_their_wait(
    tmp_path: Path,
) -> None:
    """A chunk cancelled before semaphore admission is not lost.

    With one slot and a cost-cap stop on the first call, the other chunks
    never reach the provider; they must still appear as failed, with their
    wait recorded and no provider-sized in-flight time, so the breakdown
    accounts for every chunk the run planned.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(
        tmp_path=tmp_path,
        chunk_count=3,
        max_parallel_calls=1,
        call_delay=0.01,
        stop_on_first_call=True,
    )

    timings = _timings_of(result=result)
    assert_that(result.metadata.partial).is_true()
    assert_that(timings.chunks).is_length(3)
    assert_that([chunk.failed for chunk in timings.chunks]).is_equal_to(
        [True, True, True],
    )
    # The leader is whichever chunk the semaphore admitted first, not
    # necessarily index 0 once the breakdown is sorted by chunk index. Identify
    # it by the shortest wait: a sibling cancelled just after admission can
    # briefly out-measure the leader on in-flight time under load, which made
    # the previous `max(in_flight_seconds)` pick the wrong chunk (#2315).
    leader = min(timings.chunks, key=lambda chunk: chunk.queued_seconds)
    assert_that(leader.in_flight_seconds).is_greater_than_or_equal_to(0.01)
    for chunk in timings.chunks:
        if chunk is leader:
            continue
        # Every sibling waited behind the leader's full call before the stop
        # reached it. Whether it was then cancelled while still queued or just
        # after admission depends on scheduling, so only the wait is bounded.
        assert_that(chunk.queued_seconds).is_greater_than_or_equal_to(0.01)


def test_sigterm_during_single_chunk_records_the_chunk_as_failed(
    tmp_path: Path,
) -> None:
    """A SIGTERM stop mid-call still records the lone chunk's span.

    The single-chunk fast path has its own stop handling; the chunk must
    surface as failed with zero queued time rather than vanish.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(
        tmp_path=tmp_path,
        chunk_count=1,
        call_delay=0.05,
        stop_during_first_call=True,
    )

    timings = _timings_of(result=result)
    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("SIGTERM")
    assert_that(timings.chunks).is_length(1)
    assert_that(timings.chunks[0].failed).is_true()
    assert_that(timings.chunks[0].queued_seconds).is_equal_to(0.0)


def test_resume_planning_has_its_own_span(tmp_path: Path) -> None:
    """Resume classification is accounted for in a phase, not in the gap.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=1)

    names = [span.name for span in _timings_of(result=result).phases]
    assert_that(names).contains("chunking", "resume_planning", "provider")
    assert_that(names.index("resume_planning")).is_greater_than(
        names.index("chunking"),
    )


def test_depth_two_adds_a_distinct_generated_questions_span(tmp_path: Path) -> None:
    """Depth 2 question generation is a separate span from the chunk call.

    Args:
        tmp_path: Temporary repository root.
    """
    shallow = _run(tmp_path=tmp_path, chunk_count=2, depth=1)
    deep = _run(tmp_path=tmp_path, chunk_count=2, depth=2)

    shallow_names = [span.name for span in _timings_of(result=shallow).phases]
    deep_spans = {span.name: span for span in _timings_of(result=deep).phases}

    assert_that(shallow_names).does_not_contain("generated_questions")
    assert_that(deep_spans).contains_key("generated_questions", "provider")
    # One occurrence per chunk, folded into a single span.
    assert_that(deep_spans["generated_questions"].occurrences).is_equal_to(2)


def test_generated_questions_span_is_a_strict_part_of_provider(
    tmp_path: Path,
) -> None:
    """The question span covers one call, the provider envelope covers both.

    With a single chunk at depth 2 the provider envelope spans the question
    call and the main review call back to back, so the nested span must be
    at least one stubbed call long and strictly shorter than the envelope.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=1, depth=2, call_delay=0.02)

    timings = _timings_of(result=result)
    questions = timings.phase_seconds(name="generated_questions")
    provider = timings.phase_seconds(name="provider")
    assert_that(questions).is_greater_than_or_equal_to(0.02)
    assert_that(provider).is_greater_than_or_equal_to(0.04)
    assert_that(questions).is_less_than(provider)


def test_cost_cap_serializes_chunks_and_reports_it(tmp_path: Path) -> None:
    """A spend cap forces one slot even when the config allows more.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(
        tmp_path=tmp_path,
        chunk_count=3,
        call_delay=0.02,
        max_cost_usd=100.0,
    )

    timings = _timings_of(result=result)
    assert_that(AIConfig(enabled=True).max_parallel_calls).is_greater_than(1)
    assert_that(timings.max_parallel).is_equal_to(1)
    # With one slot, the fan-out is serialized: at least one chunk waited a
    # full call behind another.
    assert_that(
        max(chunk.queued_seconds for chunk in timings.chunks),
    ).is_greater_than_or_equal_to(0.02)


def test_depth_three_adds_a_distinct_adversarial_span(tmp_path: Path) -> None:
    """Depth 3 records the adversarial sweep separately from question generation.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=1, depth=3)

    names = [span.name for span in _timings_of(result=result).phases]

    assert_that(names).contains("generated_questions", "adversarial")
    assert_that(names.index("generated_questions")).is_less_than(
        names.index("adversarial"),
    )


def test_empty_review_still_carries_a_timings_block(tmp_path: Path) -> None:
    """A no-changes review reports timings rather than a missing block.

    Args:
        tmp_path: Temporary repository root.
    """
    empty = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[],
        unified_diff="",
        pr_metadata=None,
        repo_root=str(tmp_path),
    )

    result = run_review(
        empty,
        options=ReviewSessionOptions(
            provider=_provider(),
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
            context_collection_seconds=0.25,
        ),
    )

    timings = _timings_of(result=result)
    assert_that(timings.chunks).is_empty()
    assert_that(timings.phase_seconds(name="context_collection")).is_close_to(
        0.25,
        0.001,
    )
    # Nothing ran after context collection, so that is the whole wait, and
    # the metadata duration reports the same figure.
    assert_that(timings.total_seconds).is_close_to(0.25, 0.001)
    assert_that(result.metadata.duration_seconds).is_equal_to(
        timings.total_seconds,
    )


# ---------------------------------------------------------------------------
# Rendering surfaces
# ---------------------------------------------------------------------------


def test_run_mechanics_footer_carries_the_timing_summary(tmp_path: Path) -> None:
    """The posted run-mechanics footer gains a ``Timings`` field.

    Args:
        tmp_path: Temporary repository root.
    """
    result = _run(tmp_path=tmp_path, chunk_count=2)

    mechanics = format_run_mechanics(metadata=result.metadata)

    expected = format_timing_summary(timings=_timings_of(result=result))
    assert_that(mechanics).contains(f"**Timings:** {expected}")


def test_review_body_carries_the_timing_summary(tmp_path: Path) -> None:
    """The posted review body shows the summary under its run stats.

    Args:
        tmp_path: Temporary repository root.
    """
    from lintro.ai.review.finding_matcher import match_findings
    from lintro.ai.review.github_review_body import build_review_body
    from lintro.ai.review.models.review_state import ReviewState

    result = _run(tmp_path=tmp_path, chunk_count=1)
    prior_state = ReviewState()
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha="fb740b2",
    )

    body = build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha="fb740b2",
        transport="api",
    )

    expected = format_timing_summary(timings=_timings_of(result=result))
    assert_that(body).contains(f"<sub>Timings: {expected}</sub>")


def test_sticky_comment_carries_the_timing_summary(tmp_path: Path) -> None:
    """The sticky comment shows the summary under its This-run table.

    Args:
        tmp_path: Temporary repository root.
    """
    from lintro.ai.review.sticky import build_sticky_comment

    result = _run(tmp_path=tmp_path, chunk_count=1)

    sticky = build_sticky_comment(request=StickyRequest(result=result, transport="api"))

    expected = format_timing_summary(timings=_timings_of(result=result))
    assert_that(sticky).contains(f"<sub>Timings: {expected}</sub>")


def test_run_mechanics_footer_omits_timings_when_uninstrumented() -> None:
    """Legacy metadata without timings renders the footer unchanged."""
    metadata = ReviewMetadata(
        model="gpt-4o",
        provider="openai",
        context_window=128_000,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
    )

    assert_that(format_run_mechanics(metadata=metadata)).does_not_contain("Timings")


def _uninstrumented_result() -> ReviewResult:
    """Build a result whose metadata predates timing instrumentation.

    Returns:
        A minimal result with ``metadata.timings`` left at ``None``.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="gpt-4o",
            provider="openai",
            context_window=128_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
        ),
        summary="Nothing to report.",
        findings=(),
    )


def test_json_payload_carries_null_timings_when_uninstrumented() -> None:
    """Legacy results serialize ``timings`` as ``null``, never omit the key."""
    payload = review_result_to_dict(result=_uninstrumented_result())

    assert_that(payload).contains_key("timings")
    assert_that(payload["timings"]).is_none()
    assert_that(payload["metadata"]).does_not_contain_key("timings")


def test_terminal_output_omits_summary_when_uninstrumented() -> None:
    """Legacy results render no timing line in the terminal."""
    from rich.console import Console

    from lintro.ai.review.display import render_review_terminal

    console = Console(record=True, width=200, no_color=True)

    render_review_terminal(result=_uninstrumented_result(), console=console)

    assert_that(console.export_text()).does_not_contain("total ")


def test_terminal_output_prints_the_timing_summary(tmp_path: Path) -> None:
    """The terminal renderer prints the one-line timing summary.

    Args:
        tmp_path: Temporary repository root.
    """
    from rich.console import Console

    from lintro.ai.review.display import render_review_terminal

    result = _run(tmp_path=tmp_path, chunk_count=2)
    console = Console(record=True, width=200, no_color=True)

    render_review_terminal(result=result, console=console)

    expected = format_timing_summary(timings=_timings_of(result=result))
    assert_that(expected).contains("2 chunks, max parallel 2")
    assert_that(console.export_text()).contains(expected)
