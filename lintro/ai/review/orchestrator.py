"""Review orchestrator for AI diff-based code review."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.budget import CostBudget
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIError,
    AIProviderError,
)
from lintro.ai.json_response import strip_json_fences
from lintro.ai.model_pricing import (
    calculate_available_diff_tokens,
    get_context_window,
)
from lintro.ai.review.adversarial_pass import run_adversarial_pass
from lintro.ai.review.checklist_pass import (
    GENERATED_CHECKLIST_ID_STRIDE,
    generate_extra_checklist,
    max_checklist_id,
)
from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.cli_limits import (
    assert_cli_diff_within_ceiling,
    resolve_cli_diff_budget,
    resolve_cli_findings_cap,
)
from lintro.ai.review.coverage import (
    carry_unserved_flags,
    consume_served_flags,
    inherit_same_round_paths,
    pending_invalidations_for,
)
from lintro.ai.review.custom_agent_runner import (
    CustomAgentPassResult,
    run_custom_agent_passes,
)
from lintro.ai.review.custom_agents import (
    CustomAgentSelection,
    CustomAgentSpec,
    select_custom_agents,
)
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.file_selection import resolve_file_selection
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.finding_parser import (
    reject_context_findings,
)
from lintro.ai.review.group_labels import REL_DIRECTORY_PREFIX, REL_SINGLE_FILE
from lintro.ai.review.interrupt import (
    SIGTERM_TIMEOUT_MESSAGE,
    install_review_interrupt,
    sigterm_timeout_error,
)
from lintro.ai.review.merge import (
    ChunkReviewPartial,
    finalize_partials,
    merge_checklist_answers,
    merge_file_assessments,
    merge_findings,
    merge_pr_summaries,
    merge_review_results,
    merge_verdict_reasoning,
    parse_review_response,
)
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.skipped_file import SkippedFile
from lintro.ai.review.paths_registry import generate_interaction_paths
from lintro.ai.review.progress import (
    NullReviewProgress,
    ReviewProgressCallback,
    StepTrackingProgress,
)
from lintro.ai.review.prompts import (
    PromptInputs,
    build_git_native_review_prompt,
    build_review_prompt,
    estimate_prompt_overhead,
)
from lintro.ai.review.response_pipeline import (
    ChunkReviewRequest,
    invoke_chunk_review,
    merge_response_usage,
    parse_checklist,
    parse_review_payload_with_recovery,
    payload_to_partial,
)
from lintro.ai.review.resume import filter_chunks, plan_resume, records_for_reviewed
from lintro.ai.review.sensitivity import (
    ReviewSensitivityPolicy,
    filter_findings_by_policy,
    format_strictness_prompt_section,
)
from lintro.ai.review.session import (
    ReviewSessionOptions,
    aborted_before_completion,
    cost_cap_reason,
    is_cost_cap_stop,
    is_timeout_stop,
    timeout_reason,
)
from lintro.ai.review.severity_gate import apply_cross_chunk_guard
from lintro.ai.review.state_store import state_dir, write_state_part
from lintro.ai.review.synthesis import (
    SynthesisPass,
    run_synthesis_pass,
    should_run_synthesis,
)
from lintro.ai.review.synthesis_prompt import guarded_changed_paths
from lintro.ai.review.timings import ReviewPhase, ReviewTimingRecorder
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.review.models.checklist_item import ChecklistItem
    from lintro.ai.review.models.file_classification import FileClassification
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.resume import ResumePlan
    from lintro.config.review_config import ReviewSynthesisConfig

__all__ = [
    "GENERATED_CHECKLIST_ID_STRIDE",
    "ChunkReviewPartial",
    "ChunkReviewRequest",
    "PromptInputs",
    "ReviewSessionOptions",
    "generate_extra_checklist",
    "guard_changed_paths",
    "invoke_chunk_review",
    "max_checklist_id",
    "merge_response_usage",
    "parse_checklist",
    "parse_review_payload_with_recovery",
    "payload_to_partial",
    "run_adversarial_pass",
    "build_git_native_review_prompt",
    "build_review_prompt",
    "merge_checklist_answers",
    "merge_file_assessments",
    "merge_findings",
    "merge_pr_summaries",
    "merge_review_results",
    "merge_verdict_reasoning",
    "parse_review_response",
    "resolve_review_chunks",
    "run_review",
    "run_review_async",
    "strip_json_fences",
]


def _write_incremental_coverage_part(
    *,
    collected: list[ChunkReviewPartial],
    resume: ResumePlan,
    context: ReviewContext,
    prior_state: ReviewState | None,
    force_full: bool,
    sequence: int,
    policy: ReviewSensitivityPolicy,
    stopped_reason: str = "",
) -> None:
    """Checkpoint coverage and this-run findings for a later SIGTERM.

    Writes only when ``LINTRO_REVIEW_STATE_DIR`` is set (CI artifact dir).
    ``final=True`` refreshes ``state.json`` so a leftover downloaded
    snapshot cannot last-writer-win over this run. Findings are matched
    against the original prior so a resume that skips COVERED files still
    has issues to post.

    Args:
        collected: Chunks finished so far in this run.
        resume: Resume plan for the current diff.
        context: Review diff context (head SHA).
        prior_state: Prior artifact state, if any.
        force_full: When True, do not inherit prior coverage.
        sequence: Monotonic part number for this run.
        policy: Sensitivity policy used to filter checkpoint findings.
        stopped_reason: Optional in-flight stop note stored on new records.
    """
    directory_override = os.environ.get("LINTRO_REVIEW_STATE_DIR", "").strip()
    if not directory_override:
        return
    completed_files = {path for partial in collected for path in partial.files}
    covered_now = inherit_same_round_paths(
        reviewed_now=tuple(path for path in resume.queue if path in completed_files),
        eligible_paths=resume.eligible,
        current_hashes=resume.hashes,
    )
    records = records_for_reviewed(
        plan=resume,
        reviewed_paths=covered_now,
        head_sha=context.head_ref,
        round_number=prior_state.next_round if prior_state is not None else 1,
        prior=None if force_full else prior_state,
        stopped_reason=stopped_reason,
    )
    pr_raw = os.environ.get("PR_NUMBER", "").strip()
    seed = ReviewState() if force_full or prior_state is None else prior_state
    findings = filter_findings_by_policy(
        findings=tuple(
            finding for partial in collected for finding in partial.findings
        ),
        policy=policy,
    )
    # Coverage may credit same-hash siblings; matching must not. Those
    # files were not re-read, so their prior open findings stay carried.
    actually_reviewed = frozenset(
        path for path in resume.queue if path in completed_files
    )
    match = match_findings(
        previous=seed,
        findings=findings,
        round_number=seed.next_round,
        head_sha=context.head_ref,
        reviewed_paths=actually_reviewed,
    )
    write_state_part(
        state=replace(
            seed,
            findings=match.records,
            coverage=records,
            repo=os.environ.get("GITHUB_REPOSITORY", "") or seed.repo,
            pr_number=int(pr_raw) if pr_raw.isdigit() else seed.pr_number,
            base_sha=context.base_ref or seed.base_sha,
            head_sha=context.head_ref or seed.head_sha,
            workflow="ai-review.yml",
            event=os.environ.get("GITHUB_EVENT_NAME", "") or seed.event,
            run_id=os.environ.get("GITHUB_RUN_ID", "") or seed.run_id,
        ),
        directory=state_dir(ci=True),
        sequence=sequence,
        final=True,
    )


def resolve_review_chunks(
    *,
    context: ReviewContext,
    diff_budget: int,
    classifications: list[FileClassification],
    force_semantic_chunking: bool = False,
    skipped_sink: list[SkippedFile] | None = None,
) -> list[ReviewChunk]:
    """Resolve review chunks using a budget-gated fast path.

    When the full diff fits within the token budget, return a single chunk
    without semantic splitting. Otherwise delegate to the semantic chunker.

    Args:
        context: Collected review diff context.
        diff_budget: Maximum estimated tokens available for diff content.
        classifications: Domain classifications for changed files.
        force_semantic_chunking: When True, skip the single-chunk fast path.
        skipped_sink: Optional list the chunker's per-file skips are appended
            to, so the caller can report *why* a changed file went unreviewed
            instead of only how many did (#1910).

    Returns:
        Ordered list of review chunks to process.
    """
    if not force_semantic_chunking and estimate_tokens(context.unified_diff) <= max(
        diff_budget,
        1,
    ):
        return [_single_chunk_from_context(context=context)]

    chunking = chunk_review_context(
        context=context,
        max_tokens=max(diff_budget, 1),
        classifications=classifications,
    )
    if not chunking.chunks:
        # The whole-context fallback reviews every file, so the chunker's
        # skips no longer describe what happened and must not be reported.
        return [_single_chunk_from_context(context=context)]
    if skipped_sink is not None:
        skipped_sink.extend(chunking.skipped)
    return chunking.chunks


async def _review_one_chunk_until_stop(
    *,
    chunk: ReviewChunk,
    context: ReviewContext,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int,
    checklist_items: list[ChecklistItem],
    checklist_text: str,
    classifications: list[FileClassification],
    lint_results: str | None,
    budget: CostBudget,
    progress: ReviewProgressCallback,
    repo_root: str,
    use_one_shot: bool,
    strictness_section: str,
    next_generated_checklist_id: int,
    diff_budget: int,
    stop: asyncio.Event | None,
    timings: ReviewTimingRecorder | None = None,
) -> ChunkReviewPartial:
    """Review a single chunk, aborting persistably when *stop* is set.

    Args:
        chunk: The only remaining chunk.
        context: Review diff context.
        provider: Configured AI provider.
        ai_config: Effective AI configuration.
        depth: Review depth.
        checklist_items: Selected checklist items.
        checklist_text: Pre-formatted checklist prompt.
        classifications: Domain classifications.
        lint_results: Optional lint digest.
        budget: Run cost budget.
        progress: Progress callback.
        repo_root: Workspace root for the provider.
        use_one_shot: Whether to avoid a durable CLI session.
        strictness_section: Strictness prompt fragment.
        next_generated_checklist_id: Next generated checklist id.
        diff_budget: Token budget for the diff.
        stop: Optional SIGTERM event.
        timings: Optional recorder for per-phase timing spans (#2148).

    Returns:
        The completed chunk partial.

    Raises:
        AIProviderError: When SIGTERM arrives before the chunk finishes.
    """
    # A lone chunk never waits on the concurrency semaphore, so its queued
    # time is zero by construction; only the in-flight span is measured.
    started = time.monotonic()
    review_task = asyncio.ensure_future(
        _review_chunk_with_progress(
            chunk_index=0,
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=ai_config,
            depth=depth,
            checklist_text=checklist_text,
            checklist_count=len(checklist_items),
            classifications=classifications,
            lint_results=lint_results,
            budget=budget,
            progress=progress,
            total_chunks=1,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            strictness_section=strictness_section,
            next_generated_checklist_id=next_generated_checklist_id,
            diff_budget=diff_budget,
            timings=timings,
        ),
    )

    def _record(*, failed: bool) -> None:
        """Record the lone chunk's in-flight span.

        Args:
            failed: True when the chunk ended in an error or a stop.
        """
        if timings is None:
            return
        timings.add_chunk(
            chunk_index=0,
            files=len(chunk.files),
            queued_seconds=0.0,
            in_flight_seconds=time.monotonic() - started,
            failed=failed,
        )

    if stop is None:
        unfinished = True
        try:
            single = await review_task
            unfinished = False
        finally:
            _record(failed=unfinished)
        return single
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {review_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and stop.is_set():
            if not review_task.done():
                review_task.cancel()
                with suppress(asyncio.CancelledError):
                    await review_task
                _record(failed=True)
                raise AIProviderError(SIGTERM_TIMEOUT_MESSAGE) from TimeoutError(
                    "SIGTERM",
                )
            # The agent may have died from a forwarded TERM before the
            # stop task won. Treat that failure as the persistable
            # SIGTERM timeout instead of aborting without coverage.
            if review_task.cancelled() or review_task.exception() is not None:
                _record(failed=True)
                raise AIProviderError(SIGTERM_TIMEOUT_MESSAGE) from TimeoutError(
                    "SIGTERM",
                )
            _record(failed=False)
            return review_task.result()
        unfinished = True
        try:
            single = await review_task
            unfinished = False
        finally:
            _record(failed=unfinished)
        return single
    finally:
        if not stop_task.done():
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task


async def _review_all_chunks(
    *,
    chunks: list[ReviewChunk],
    context: ReviewContext,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int,
    checklist_items: list[ChecklistItem],
    checklist_text: str,
    classifications: list[FileClassification],
    lint_results: str | None,
    budget: CostBudget,
    progress: ReviewProgressCallback,
    repo_root: str,
    use_one_shot: bool,
    max_parallel_calls: int,
    strictness_section: str,
    next_generated_checklist_id: int = 1,
    diff_budget: int,
    completed_sink: list[ChunkReviewPartial] | None = None,
    on_chunk_complete: Callable[[list[ChunkReviewPartial]], None] | None = None,
    stop: asyncio.Event | None = None,
    timings: ReviewTimingRecorder | None = None,
) -> list[ChunkReviewPartial]:
    """Review all chunks with bounded concurrency.

    When ``completed_sink`` is provided, each successfully reviewed chunk's
    partial is appended to it as soon as it completes. This lets the caller
    recover the chunks reviewed so far if the run aborts mid-way (e.g. the cost
    cap is reached), enabling a graceful partial review instead of discarding
    all completed work. ``on_chunk_complete`` is invoked with the sink after
    each append so CI can write an incremental coverage part.

    Chunks are reviewed concurrently under a semaphore capped by
    ``max_parallel_calls``. Callers that enforce a cost cap pass ``1`` so the
    resume queue cannot invert (issue #2154). A ``ReviewExecutionError`` or a
    cost-cap stop cancels the remaining work and propagates to
    ``run_review_async``. Depth ≥ 2 assigns each chunk a disjoint
    generated-checklist id range so merge stays deterministic under fan-out.
    ``stop`` is set by a SIGTERM/SIGINT handler so an in-flight chunk can
    be cancelled and completed siblings persisted (#2156). ``timings`` records
    each chunk's semaphore-queued and in-flight split (#2148).
    """
    if len(chunks) <= 1:
        single = await _review_one_chunk_until_stop(
            chunk=chunks[0],
            context=context,
            provider=provider,
            ai_config=ai_config,
            depth=depth,
            checklist_items=checklist_items,
            checklist_text=checklist_text,
            classifications=classifications,
            lint_results=lint_results,
            budget=budget,
            progress=progress,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            strictness_section=strictness_section,
            next_generated_checklist_id=next_generated_checklist_id,
            diff_budget=diff_budget,
            stop=stop,
            timings=timings,
        )
        if completed_sink is not None:
            completed_sink.append(single)
            if on_chunk_complete is not None:
                on_chunk_complete(completed_sink)
        return [single]

    partials: list[ChunkReviewPartial | None] = [None] * len(chunks)
    max_workers = min(len(chunks), max_parallel_calls)
    first_error: ReviewExecutionError | None = None

    # Bounded concurrency on the caller's event loop: chunk reviews are provider
    # I/O, so tasks under a semaphore keep the ``max_parallel_calls`` ceiling
    # without threads. A cost cap does not force serial execution; see
    # ``CostBudget.execute`` for the accepted n−1-call overshoot bound.
    semaphore = asyncio.Semaphore(max_workers)

    async def _run_chunk(
        chunk_index: int,
        chunk: ReviewChunk,
    ) -> tuple[int, ChunkReviewPartial | Exception]:
        """Review one chunk, returning its index alongside the outcome.

        Failures are returned rather than raised so the caller keeps the
        chunk-to-outcome mapping that ``as_completed`` would otherwise lose.

        Args:
            chunk_index: Position of the chunk in the run.
            chunk: The chunk to review.

        Returns:
            The chunk index paired with its partial or the exception raised.
        """
        chunk_checklist_id = (
            next_generated_checklist_id + chunk_index * GENERATED_CHECKLIST_ID_STRIDE
        )
        # Queued time is measured from task creation to semaphore admission,
        # so a run bottlenecked by ``max_parallel_calls`` is distinguishable
        # from one bottlenecked by provider latency (#2148).
        queued_at = time.monotonic()
        admitted_at: float | None = None
        failed = True
        try:
            async with semaphore:
                admitted_at = time.monotonic()
                outcome = chunk_index, await _review_chunk_with_progress(
                    chunk_index=chunk_index,
                    chunk=chunk,
                    context=context,
                    provider=provider,
                    ai_config=ai_config,
                    depth=depth,
                    checklist_text=checklist_text,
                    checklist_count=len(checklist_items),
                    classifications=classifications,
                    lint_results=lint_results,
                    budget=budget,
                    progress=StepTrackingProgress(progress),
                    total_chunks=len(chunks),
                    repo_root=repo_root,
                    use_one_shot=use_one_shot,
                    strictness_section=strictness_section,
                    next_generated_checklist_id=chunk_checklist_id,
                    diff_budget=diff_budget,
                    timings=timings,
                )
        except Exception as exc:
            return chunk_index, exc
        else:
            failed = False
            return outcome
        finally:
            # Recorded outside the semaphore so a chunk cancelled while still
            # queued (cost-cap stop, SIGTERM) reports its wait with no
            # in-flight time rather than vanishing from the breakdown.
            if timings is not None:
                now = time.monotonic()
                timings.add_chunk(
                    chunk_index=chunk_index,
                    files=len(chunk.files),
                    queued_seconds=(
                        (admitted_at if admitted_at is not None else now) - queued_at
                    ),
                    in_flight_seconds=(
                        now - admitted_at if admitted_at is not None else 0.0
                    ),
                    failed=failed,
                )

    review_tasks = [
        asyncio.ensure_future(_run_chunk(chunk_index, chunk))
        for chunk_index, chunk in enumerate(chunks)
    ]
    stop_task: asyncio.Future[tuple[int, ChunkReviewPartial | Exception]] | None
    stop_task = None
    if stop is not None:
        interrupt = stop

        async def _stop_as_timeout() -> tuple[int, ChunkReviewPartial | Exception]:
            """Surface SIGTERM as a persistable timeout outcome."""
            await interrupt.wait()
            return -1, sigterm_timeout_error()

        stop_task = asyncio.ensure_future(_stop_as_timeout())
    tasks: list[asyncio.Future[tuple[int, ChunkReviewPartial | Exception]]] = [
        *review_tasks,
    ]
    if stop_task is not None:
        tasks.append(stop_task)

    completed = 0
    remaining_reviews = len(review_tasks)
    try:
        for finished in asyncio.as_completed(tasks):
            chunk_index, outcome = await finished
            if chunk_index >= 0:
                remaining_reviews -= 1
            if (
                isinstance(outcome, Exception)
                and stop is not None
                and stop.is_set()
                and not is_cost_cap_stop(exc=outcome)
            ):
                # A forwarded TERM can kill the isolated agent and surface
                # a non-timeout provider error. Persist as SIGTERM anyway.
                outcome = sigterm_timeout_error()
            graceful_stop = isinstance(
                outcome,
                (ReviewExecutionError, AICostBudgetExceededError),
            ) or (isinstance(outcome, Exception) and is_timeout_stop(exc=outcome))
            if graceful_stop:
                # A cost-cap / timeout / SIGTERM stop is an expected halt.
                # Harvest siblings that already finished so a timeout on
                # one worker cannot drop coverage the other worker wrote.
                for task in tasks:
                    if not task.done() or task.cancelled():
                        continue
                    try:
                        other_index, other = task.result()
                    except Exception:
                        logger.opt(exception=True).debug(
                            "Skipping a failed sibling while harvesting "
                            "completed chunks",
                        )
                        continue  # nosec B112 - harvest only finished siblings; a failed result() is not this stop's outcome
                    if isinstance(other, Exception):
                        continue
                    if partials[other_index] is not None:
                        continue
                    partials[other_index] = other
                    if completed_sink is not None:
                        completed_sink.append(other)
                        if on_chunk_complete is not None:
                            on_chunk_complete(completed_sink)
                if isinstance(outcome, (AIError, ReviewExecutionError)):
                    raise outcome
            if isinstance(outcome, Exception):
                if first_error is None:
                    first_error = aborted_before_completion(
                        cause=outcome,
                        provider=provider,
                        chunk_index=chunk_index,
                        total_chunks=len(chunks),
                        step="reviewing",
                        completed_chunks=completed,
                    )
                break
            partials[chunk_index] = outcome
            if completed_sink is not None:
                completed_sink.append(outcome)
                if on_chunk_complete is not None:
                    on_chunk_complete(completed_sink)
            completed += 1
            if remaining_reviews == 0:
                break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Drain the cancellations so no chunk review (and no CLI child
        # process) outlives this function.
        await asyncio.gather(*tasks, return_exceptions=True)

    if first_error is not None:
        raise first_error

    # Index-keyed merge keeps completion order from scrambling chunk order.
    return [partial for partial in partials if partial is not None]


async def _review_chunk_with_progress(
    *,
    chunk_index: int,
    chunk: ReviewChunk,
    context: ReviewContext,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int,
    checklist_text: str,
    checklist_count: int,
    classifications: list[FileClassification],
    lint_results: str | None,
    budget: CostBudget,
    progress: ReviewProgressCallback,
    total_chunks: int,
    repo_root: str,
    use_one_shot: bool,
    strictness_section: str = "",
    next_generated_checklist_id: int = 1,
    diff_budget: int = 0,
    timings: ReviewTimingRecorder | None = None,
) -> ChunkReviewPartial:
    """Review one chunk with progress tracking and error wrapping.

    A cost-cap stop is re-raised raw; any other failure is wrapped as a
    ``ReviewExecutionError`` after the progress tracker is notified.
    ``timings`` records the chunk's intra-chunk phase spans (#2148).
    """
    budget.check()
    progress.on_chunk_start(chunk_index=chunk_index, files=list(chunk.files))
    try:
        partial, _next_id = await _review_chunk(
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=ai_config,
            depth=depth,
            checklist_text=checklist_text,
            checklist_count=checklist_count,
            next_generated_checklist_id=next_generated_checklist_id,
            classifications=classifications,
            lint_results=lint_results,
            budget=budget,
            progress=progress,
            chunk_index=chunk_index,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            strictness_section=strictness_section,
            diff_budget=diff_budget,
            timings=timings,
        )
    except Exception as exc:
        # A cost-cap stop is an expected graceful halt, not a chunk failure:
        # re-raise it raw so run_review can finalize a partial cleanly.
        if is_cost_cap_stop(exc=exc):
            raise
        step_tracker = progress if isinstance(progress, StepTrackingProgress) else None
        last_step = step_tracker.last_step if step_tracker else "reviewing"
        progress.on_error(
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            step=last_step,
            completed_chunks=chunk_index,
            error=exc,
        )
        raise aborted_before_completion(
            cause=exc,
            provider=provider,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            step=last_step,
            completed_chunks=chunk_index,
        ) from exc
    progress.on_chunk_done(chunk_index=chunk_index)
    return partial


def run_review(
    context: ReviewContext,
    *,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int = 1,
    checklist_items: list[ChecklistItem],
    checklist_text: str,
    classifications: list[FileClassification],
    context_window_override: int | None = None,
    lint_results: str | None = None,
    progress: ReviewProgressCallback | None = None,
    sensitivity: ReviewSensitivityPolicy | None = None,
    force_semantic_chunking: bool = False,
    timeout: float | None = None,
    custom_agents: tuple[CustomAgentSpec, ...] = (),
    run_builtin_checklist: bool = True,
    workspace_root: Path | None = None,
    context_collection_seconds: float = 0.0,
    prior_state: ReviewState | None = None,
    force_full: bool = False,
    enforce_cost_cap: bool = True,
    stop: asyncio.Event | None = None,
    synthesis: ReviewSynthesisConfig | None = None,
) -> ReviewResult:
    """Execute an AI diff review from synchronous code.

    This is the sync/async boundary for ``lintro review``: the review
    pipeline below it is async, and ``asyncio.run`` is entered exactly
    once here so one event loop (and one provider client) serves the
    whole review. It is also where the keyword wall ends — the options
    are packed into a :class:`~lintro.ai.review.session.ReviewSessionOptions`
    and every layer below takes that object (#2301).

    Args:
        context: Collected review diff context.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and fallbacks.
        depth: Review depth level (1-3).
        checklist_items: Selected checklist items for the review.
        checklist_text: Pre-formatted checklist prompt text.
        classifications: Domain classifications for changed files.
        context_window_override: Optional explicit context window override.
        lint_results: Optional lint digest for ``--with-lint`` integration.
        progress: Optional progress callback for live status updates.
        sensitivity: Sensitivity preset controlling prompts and filters.
        force_semantic_chunking: When True, skip the single-chunk fast path.
        timeout: Optional per-call timeout override in seconds.
        custom_agents: Discovered user-defined review agents (issue #1245).
        run_builtin_checklist: When False, skip the built-in checklist passes
            and run only the custom agents (``review.custom_agents: only``).
        workspace_root: Optional workspace root used to build providers for
            agents that declare a ``model`` override.
        context_collection_seconds: Wall-clock seconds the caller spent in
            ``collect_review_context`` (recorded in ``phase_timings``).
        prior_state: Artifact or local-ledger state from a previous round.
        force_full: Discard carried coverage (``--full``).
        enforce_cost_cap: When True, honor ``ai.max_cost_usd`` and serialize
            chunk calls so concurrency cannot violate queue order.
        stop: Optional event set to persist and halt (tests inject this).
        synthesis: Cross-chunk synthesis configuration (#2269). ``None`` or a
            disabled config means no extra pass runs.

    Returns:
        Complete review result with metadata, checklist, and findings.
    """
    return asyncio.run(
        run_review_async(
            context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=ai_config,
                depth=depth,
                checklist_items=checklist_items,
                checklist_text=checklist_text,
                classifications=classifications,
                context_window_override=context_window_override,
                lint_results=lint_results,
                progress=progress,
                sensitivity=sensitivity,
                force_semantic_chunking=force_semantic_chunking,
                timeout=timeout,
                custom_agents=custom_agents,
                run_builtin_checklist=run_builtin_checklist,
                workspace_root=workspace_root,
                context_collection_seconds=context_collection_seconds,
                prior_state=prior_state,
                force_full=force_full,
                enforce_cost_cap=enforce_cost_cap,
                stop=stop,
                synthesis=synthesis,
            ),
        ),
    )


def guard_changed_paths(*, context: ReviewContext) -> tuple[str, ...]:
    """Return every path the cross-chunk guard treats as changed by the PR.

    One implementation, re-exported. It lives in
    :mod:`lintro.ai.review.synthesis_prompt` because the synthesis pass needs
    the same list and the dependency only runs one way — this module imports
    the pass, which imports that module — so the reverse import would close a
    cycle.

    Args:
        context: Collected review context.

    Returns:
        Changed paths and rename/copy sources, in changed-file order.
    """
    return guarded_changed_paths(context=context)


async def run_review_async(
    context: ReviewContext,
    *,
    options: ReviewSessionOptions,
) -> ReviewResult:
    """Execute an AI diff review with depth-controlled passes.

    Args:
        context: Collected review diff context.
        options: Session options for the run — provider, AI config, depth,
            checklist, sensitivity, resume state, and stop event. See
            :class:`~lintro.ai.review.session.ReviewSessionOptions`.

    Returns:
        Complete review result with metadata, checklist, and findings.

    Raises:
        ValueError: If ``options.depth`` is outside the allowed range 1-3.
        AIError: When the review fails for a non-recoverable reason (e.g.
            provider authentication or a genuine provider error). A cost-cap
            stop is handled internally and returned as a partial result instead.
        ReviewExecutionError: When a chunk fails mid-run for a reason other than
            the cost cap.
    """
    if options.depth < 1 or options.depth > 3:
        raise ValueError(f"depth must be between 1 and 3, got {options.depth}")

    review_sensitivity = options.sensitivity or ReviewSensitivityPolicy(
        strictness=ReviewStrictness.BALANCED,
        report_migration_notes=True,
        report_doc_drift=True,
        report_test_gaps=True,
    )
    strictness_section = format_strictness_prompt_section(policy=review_sensitivity)

    if not context.changed_files and not context.unified_diff.strip():
        return _empty_review_result(
            context=context,
            provider=options.provider,
            depth=options.depth,
            checklist_items=options.checklist_items,
            context_window_override=options.context_window_override,
            context_collection_seconds=options.context_collection_seconds,
        )

    # One monotonic clock for the whole run: the recorder is back-dated by the
    # context-collection time already spent so ``total_seconds`` (and the
    # reported duration) covers the whole wait, not just the phases after the
    # early-return (#2148).
    timings = ReviewTimingRecorder(
        started_at=time.monotonic() - max(options.context_collection_seconds, 0.0),
    )
    timings.add_phase(
        name=ReviewPhase.CONTEXT_COLLECTION,
        seconds=options.context_collection_seconds,
    )

    context_window = get_context_window(
        model=options.provider.model_name,
        override=options.context_window_override,
    )
    prompt_overhead = estimate_prompt_overhead(
        context=context,
        checklist_text=options.checklist_text,
        classifications=options.classifications,
        lint_results=options.lint_results,
    )
    diff_budget = calculate_available_diff_tokens(
        context_window=context_window,
        prompt_overhead=prompt_overhead,
    )
    if options.ai_config.transport == AITransport.CLI:
        # Context-window budgets are transport-blind and leave ~1.5k-line PRs
        # as a single CLI chunk (#1967). Tighten before the chunker runs, and
        # refuse outright when the full diff exceeds the hard ceiling.
        assert_cli_diff_within_ceiling(
            context=context,
            cli_max_diff_bytes=options.ai_config.cli_max_diff_bytes,
        )
        diff_budget = resolve_cli_diff_budget(
            context_window_budget=diff_budget,
            cli_max_diff_tokens=options.ai_config.cli_max_diff_tokens,
        )
    chunk_skips: list[SkippedFile] = []
    with timings.phase(name=ReviewPhase.CHUNKING):
        chunks = (
            resolve_review_chunks(
                context=context,
                diff_budget=diff_budget,
                classifications=options.classifications,
                force_semantic_chunking=options.force_semantic_chunking,
                skipped_sink=chunk_skips,
            )
            if options.run_builtin_checklist
            else []
        )
    # Resume planning hashes every file patch and walks importers over the
    # post-image set, so its cost scales with the diff; it gets its own span
    # rather than hiding in the gap between the phase sum and the total.
    with timings.phase(name=ReviewPhase.RESUME_PLANNING):
        resume = plan_resume(
            context=context,
            prior=options.prior_state,
            extra_skips=chunk_skips,
            groups=tuple(tuple(chunk.files) for chunk in chunks),
            force_full=options.force_full,
        )
        if resume.queue:
            chunks = filter_chunks(chunks=chunks, queue=resume.queue)
        elif options.run_builtin_checklist:
            chunks = []
    agent_selection = select_custom_agents(
        agents=options.custom_agents,
        changed_paths=tuple(file.path for file in context.changed_files),
    )
    for skipped_agent in agent_selection.skipped:
        logger.info(
            "Skipping custom review agent {agent}: {reason}",
            agent=skipped_agent.agent.name,
            reason=skipped_agent.reason.value,
        )

    effective_ai_config = (
        options.ai_config.model_copy(update={"api_timeout": options.timeout})
        if options.timeout is not None
        else options.ai_config
    )
    tracker = options.progress or NullReviewProgress()
    budget = CostBudget(
        max_cost_usd=(
            options.ai_config.max_cost_usd if options.enforce_cost_cap else None
        ),
    )
    # A cost cap serializes chunk calls so the resume queue cannot invert
    # (#2154); the effective ceiling is reported alongside the timings so a
    # slow run's concurrency is never guessed at.
    max_parallel_calls = (
        1
        if options.enforce_cost_cap and options.ai_config.max_cost_usd is not None
        else options.ai_config.max_parallel_calls
    )
    effective_max_parallel = max(min(len(chunks), max_parallel_calls), 1)
    # Branch on the provider's declared capability, not its identity (#1241):
    # a durable session only helps when the transport can resume one.
    # begin/end_durable_session are concrete no-ops on BaseAIProvider, so no
    # hasattr guard is needed -- every provider answers them.
    use_durable_session = (
        options.provider.capabilities.supports_sessions and len(chunks) == 1
    )
    repo_root = context.repo_root or os.getcwd()
    use_one_shot = len(chunks) > 1

    total_findings = 0
    completed = False
    partial = False
    durable_session_started = False
    stopped_reason = ""
    collected: list[ChunkReviewPartial] = []
    partials: list[ChunkReviewPartial] = []
    custom_results: list[CustomAgentPassResult] = []
    custom_agents_failed: list[str] = []
    synthesis_pass: SynthesisPass | None = None
    merged = merge_review_results(partials=partials)
    filtered_findings: tuple[ReviewFinding, ...] = ()
    custom_findings: tuple[ReviewFinding, ...] = ()
    started_at = timings.started_at
    provider_started = time.monotonic()
    provider_seconds = 0.0
    parse_merge_seconds = 0.0
    interrupt = options.stop if options.stop is not None else asyncio.Event()
    uninstall_interrupt = install_review_interrupt(interrupt)
    try:
        # Open the session inside the try so a failure before or during
        # on_start() still reaches the finally that tears it down.
        if use_durable_session:
            options.provider.begin_durable_session(repo_root=repo_root)
            durable_session_started = True
        tracker.on_start(total_chunks=len(chunks), depth=options.depth)
        provider_started = time.monotonic()
        if chunks:
            part_seq = 0

            def _checkpoint(done: list[ChunkReviewPartial]) -> None:
                """Write an incremental coverage part after each finished chunk."""
                nonlocal part_seq
                next_seq = part_seq + 1
                try:
                    _write_incremental_coverage_part(
                        collected=done,
                        resume=resume,
                        context=context,
                        prior_state=options.prior_state,
                        force_full=options.force_full,
                        sequence=next_seq,
                        policy=review_sensitivity,
                    )
                except Exception:
                    logger.opt(exception=True).warning(
                        "Could not write incremental review-resume part {n}",
                        n=next_seq,
                    )
                else:
                    part_seq = next_seq

            partials = await _review_all_chunks(
                chunks=chunks,
                context=context,
                provider=options.provider,
                ai_config=effective_ai_config,
                depth=options.depth,
                checklist_items=options.checklist_items,
                checklist_text=options.checklist_text,
                classifications=options.classifications,
                lint_results=options.lint_results,
                budget=budget,
                progress=tracker,
                repo_root=repo_root,
                use_one_shot=use_one_shot,
                max_parallel_calls=max_parallel_calls,
                strictness_section=strictness_section,
                next_generated_checklist_id=(
                    max_checklist_id(checklist_items=options.checklist_items) + 1
                ),
                diff_budget=diff_budget,
                completed_sink=collected,
                on_chunk_complete=_checkpoint,
                stop=interrupt,
                timings=timings,
            )
        if resume.queue:
            await run_custom_agent_passes(
                selected=agent_selection.selected,
                context=context,
                provider=options.provider,
                ai_config=effective_ai_config,
                budget=budget,
                repo_root=repo_root,
                workspace_root=options.workspace_root,
                # Never reuse the built-in review's durable session: each agent
                # is an independent, narrowly scoped pass with its own
                # instructions.
                use_one_shot=True,
                on_pass_complete=custom_results.append,
                on_agent_failed=custom_agents_failed.append,
            )
        provider_seconds = time.monotonic() - provider_started
        timings.add_phase(name=ReviewPhase.PROVIDER, seconds=provider_seconds)
        merge_started = time.monotonic()
        merged, filtered_findings, total_findings = finalize_partials(
            partials=partials,
            policy=review_sensitivity,
        )
        # Custom agent findings bypass the run-level sensitivity filter: each
        # agent declares its own strictness and severity policy, so a
        # run-level preset must not silently drop what a maintainer
        # explicitly asked to be checked. Merged here, before the finally
        # block, so tracker.on_complete's count includes them.
        custom_findings = tuple(
            finding for result in custom_results for finding in result.findings
        )
        filtered_findings = filtered_findings + custom_findings
        total_findings = len(filtered_findings)
        parse_merge_seconds = time.monotonic() - merge_started
        timings.add_phase(name=ReviewPhase.PARSE_MERGE, seconds=parse_merge_seconds)
        # --- cross-chunk synthesis seam (#2269) -------------------------------
        # The one place the optional whole-PR pass hooks in: after the chunk
        # findings are merged and filtered, before the result is assembled.
        # Everything the pass does lives in lintro.ai.review.synthesis, so
        # #1972 Phase 4 can move this call without touching the pass itself.
        # Only the completed path runs it: a review already stopped by a cost
        # cap or a timeout must not spend another call.
        if should_run_synthesis(
            config=options.synthesis,
            chunks_reviewed=len(partials),
        ):
            # ``should_run_synthesis`` already rejected a None config; bind it
            # so the type checker knows that too.
            synthesis_config = options.synthesis
            assert synthesis_config is not None
            with timings.phase(name=ReviewPhase.SYNTHESIS):
                synthesis_pass = await run_synthesis_pass(
                    context=context,
                    summaries=_chunk_summaries(chunks=chunks, partials=partials),
                    existing_findings=filtered_findings,
                    provider=options.provider,
                    ai_config=effective_ai_config,
                    config=synthesis_config,
                    policy=review_sensitivity,
                    budget=budget,
                    repo_root=repo_root,
                    # Never reuse the built-in review's durable session: the
                    # pass is a standalone whole-PR question, not a chunk.
                    use_one_shot=True,
                    diff_budget=diff_budget,
                    # The chunk fan-out already raced this event so a SIGTERM
                    # can persist coverage inside the runner's shutdown
                    # window; the extra call gets the same treatment, and a
                    # stop that lands here is recorded as a failed pass.
                    stop=interrupt,
                )
            filtered_findings = filtered_findings + synthesis_pass.findings
            total_findings = len(filtered_findings)
        completed = True
    except (AIError, ReviewExecutionError) as exc:
        # A graceful partial review: a cost cap or timeout stopped the run
        # mid-way (#1094 / #2154). Keep the chunks reviewed so far instead of
        # discarding completed work. Detected from the raised exception, never
        # inferred from residual budget. Any other failure (auth, provider,
        # parser) must propagate so callers surface a real error via the #1101
        # taxonomy. When the stop trips before ANY chunk completes,
        # ``collected`` is empty and the partial is empty-but-actionable
        # rather than a generic abort.
        if is_cost_cap_stop(exc=exc):
            stopped_reason = cost_cap_reason(cap=budget.max_cost_usd)
        elif is_timeout_stop(exc=exc):
            stopped_reason = timeout_reason(exc=exc)
        else:
            raise
        if provider_seconds <= 0.0:
            provider_seconds = time.monotonic() - provider_started
            timings.add_phase(name=ReviewPhase.PROVIDER, seconds=provider_seconds)
        partials = list(collected)
        partial = True
        merge_started = time.monotonic()
        merged, filtered_findings, total_findings = finalize_partials(
            partials=partials,
            policy=review_sensitivity,
        )
        custom_findings = tuple(
            finding for result in custom_results for finding in result.findings
        )
        filtered_findings = filtered_findings + custom_findings
        total_findings = len(filtered_findings)
        parse_merge_seconds = time.monotonic() - merge_started
        timings.add_phase(name=ReviewPhase.PARSE_MERGE, seconds=parse_merge_seconds)
        completed = True
        if "SIGTERM" in stopped_reason:
            hint = (
                "The runner sent SIGTERM; coverage was persisted. "
                "Re-run to resume remaining files."
            )
        elif stopped_reason.startswith("timeout"):
            timeout_setting = (
                "ai.transports.cli.timeout"
                if options.ai_config.transport is AITransport.CLI
                else "ai.transports.api.timeout"
            )
            hint = f"Raise {timeout_setting} or narrow --path to review the rest."
        else:
            hint = "Raise ai.max_cost_usd or narrow --path to review the rest."
        logger.warning(
            "Review stopped early — {reason} after reviewing {n} of {m} chunks. {hint}",
            reason=stopped_reason,
            hint=hint,
            n=len(partials),
            m=len(chunks),
            cause=str(exc),
        )
    finally:
        # The validation span opens before cleanup so a slow durable-session
        # close or progress callback lands in a phase, not only in the total.
        validation_started = time.monotonic()
        uninstall_interrupt()
        if durable_session_started:
            options.provider.end_durable_session()
        with suppress(Exception):
            if completed:
                tracker.on_complete(total_findings=total_findings)
            else:
                tracker.on_abort()

    # ``phase_timings`` stays the flat three-key mapping earlier consumers
    # (MCP run payloads, eval stamps) already read; ``timings`` carries the
    # ordered spans and the per-chunk detail.
    phase_timings = {
        "context_collection": max(options.context_collection_seconds, 0.0),
        "provider": max(provider_seconds, 0.0),
        "parse_merge": max(parse_merge_seconds, 0.0),
    }

    # The synthesis pass is one more provider call against the same budget, so
    # its usage joins the run totals rather than hiding outside them (#2269).
    total_input = (
        sum(item.input_tokens for item in partials)
        + sum(result.input_tokens for result in custom_results)
        + (synthesis_pass.input_tokens if synthesis_pass is not None else 0)
    )
    total_output = (
        sum(item.output_tokens for item in partials)
        + sum(result.output_tokens for result in custom_results)
        + (synthesis_pass.output_tokens if synthesis_pass is not None else 0)
    )
    total_cost = (
        sum(item.cost_estimate for item in partials)
        + sum(result.cost_estimate for result in custom_results)
        + (synthesis_pass.cost_estimate if synthesis_pass is not None else 0.0)
    )
    chunks_reviewed = len(partials)
    summary = (
        _custom_agents_only_summary(
            run_builtin_checklist=options.run_builtin_checklist,
            agents_run=len(custom_results),
            findings=len(custom_findings),
        )
        or merged.summary
    )

    selection = resolve_file_selection(
        context=context,
        chunk_skips=[
            *chunk_skips,
            *_agent_scope_skips(
                context=context,
                selection=agent_selection,
                run_builtin_checklist=options.run_builtin_checklist,
                completed_agents=frozenset(
                    result.agent_name for result in custom_results
                ),
            ),
        ],
    )

    metadata = ReviewMetadata(
        model=options.provider.model_name,
        provider=options.provider.name,
        context_window=context_window,
        depth=options.depth,
        strictness=review_sensitivity.strictness.value,
        chunks_total=len(chunks),
        chunks_current=chunks_reviewed,
        files_reviewed=len(selection.reviewed_paths),
        files_total=len(selection.reviewed_paths) + len(selection.skipped),
        reviewed_paths=selection.reviewed_paths,
        skipped_files=selection.skipped,
        checklist_items=len(options.checklist_items),
        token_usage={
            "prompt": total_input,
            "completion": total_output,
            "total": total_input + total_output,
        },
        cost_estimate_usd=total_cost,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        timestamp=datetime.now(tz=UTC).isoformat(),
        token_usage_estimated=options.ai_config.transport == AITransport.CLI,
        partial=partial,
        chunks_reviewed=chunks_reviewed,
        stopped_reason=stopped_reason,
        phase_timings=phase_timings,
        custom_agents_run=len(custom_results),
        custom_agents_skipped=(
            len(agent_selection.skipped) + len(custom_agents_failed)
        ),
        coverage_degradations=(
            *(
                degradation
                for item in partials
                for degradation in item.coverage_degradations
            ),
            *(synthesis_pass.degradations if synthesis_pass is not None else ()),
        ),
        synthesis=synthesis_pass.outcome if synthesis_pass is not None else None,
    )

    completed_files = {path for partial in partials for path in partial.files}
    agent_files = {path for item in custom_results for path in item.files}
    actually_reviewed = tuple(
        path for path in resume.queue if path in completed_files or path in agent_files
    )
    covered_now = inherit_same_round_paths(
        reviewed_now=actually_reviewed,
        eligible_paths=resume.eligible,
        current_hashes=resume.hashes,
    )
    coverage = resume.counts(reviewed_now=covered_now)
    coverage_records = records_for_reviewed(
        plan=resume,
        reviewed_paths=covered_now,
        head_sha=context.head_ref,
        round_number=(
            options.prior_state.next_round if options.prior_state is not None else 1
        ),
        prior=None if options.force_full else options.prior_state,
        stopped_reason=stopped_reason,
    )
    payload_flags = tuple(
        flag for partial in partials for flag in partial.flagged_files
    )
    filtered_findings, converted_flags = reject_context_findings(
        findings=filtered_findings,
        allowed_paths=set(resume.queue),
        eligible_paths=set(resume.eligible),
    )
    # #2265: a chunk only ever sees the other files at the base commit, so a
    # finding asserting that a file this PR changed was never touched is
    # reporting its own blind spot. The guard runs over the run's full changed
    # set, not the chunk's, and downgrades rather than drops.
    filtered_findings = apply_cross_chunk_guard(
        findings=filtered_findings,
        changed_paths=guard_changed_paths(context=context),
    )
    if synthesis_pass is not None:
        # Both passes above run *after* the synthesis pass returned its own
        # tally, and on a resumed run either can convert or discard one of its
        # findings — ``reject_context_findings`` drops a finding on a path
        # this round was not asked to re-review. The number every surface
        # renders must be the number that actually survived, so it is counted
        # from what is left rather than carried over from the pass.
        metadata = replace(
            metadata,
            synthesis=replace(
                synthesis_pass.outcome,
                findings_added=sum(
                    1
                    for finding in filtered_findings
                    if finding.origin is FindingOrigin.SYNTHESIS
                ),
            ),
        )
    prior_flags = (
        options.prior_state.flagged_files if options.prior_state is not None else ()
    )
    prior_consumed = (
        ()
        if options.force_full or options.prior_state is None
        else options.prior_state.consumed_flags
    )
    flagged_files = carry_unserved_flags(
        new_flags=(*payload_flags, *converted_flags),
        prior_flags=prior_flags,
        covered_now=covered_now,
    )
    consumed_flags = consume_served_flags(
        prior_consumed=prior_consumed,
        flags=(*payload_flags, *converted_flags, *prior_flags),
        covered_now=covered_now,
        current_hashes=resume.hashes,
    )
    awaiting_paths = tuple(
        item.path
        for item in resume.classified
        if item.need is not FileReviewNeed.COVERED and item.path not in covered_now
    )
    awaiting_reasons = tuple(
        (item.path, item.flag_reason)
        for item in resume.classified
        if item.path in set(awaiting_paths) and item.flag_reason
    )
    # The validation span (context-finding rejection, coverage and resume
    # bookkeeping, flag reconciliation) and the run total are closed here so
    # the breakdown covers everything the caller waited for (#2148).
    timings.add_phase(
        name=ReviewPhase.VALIDATION,
        seconds=time.monotonic() - validation_started,
    )
    duration_seconds = time.monotonic() - started_at
    metadata = replace(
        metadata,
        reviewed_paths=actually_reviewed,
        files_reviewed=len(actually_reviewed),
        duration_seconds=duration_seconds,
        timings=timings.build(
            total_seconds=duration_seconds,
            max_parallel=effective_max_parallel,
        ),
    )

    return ReviewResult(
        metadata=metadata,
        summary=summary,
        checklist=merged.checklist,
        findings=filtered_findings,
        pr_summary=merged.pr_summary,
        verdict_reasoning=merged.verdict_reasoning,
        file_assessments=merged.file_assessments,
        coverage=coverage,
        coverage_records=coverage_records,
        flagged_files=flagged_files,
        awaiting_paths=awaiting_paths,
        awaiting_reasons=awaiting_reasons,
        pending_invalidations=pending_invalidations_for(
            classified=resume.classified,
            reviewed_now=covered_now,
        ),
        consumed_flags=consumed_flags,
    )


def _chunk_summaries(
    *,
    chunks: list[ReviewChunk],
    partials: list[ChunkReviewPartial],
) -> tuple[ChunkSummary, ...]:
    """Build the per-chunk digest the cross-chunk synthesis pass reads.

    Args:
        chunks: Chunks planned for this run, in plan order.
        partials: Completed chunk partials, in completion order.

    Returns:
        One digest per completed chunk. The chunk id is recovered from the
        plan by file set so the digest names the same chunk the reader sees
        elsewhere; a partial that matches no planned chunk falls back to its
        position, which keeps the digest readable rather than blank.
    """
    ids = {tuple(chunk.files): chunk.id for chunk in chunks}
    return tuple(
        ChunkSummary(
            chunk_id=ids.get(tuple(item.files), position),
            files=tuple(item.files),
            findings=item.findings,
        )
        for position, item in enumerate(partials, start=1)
    )


def _custom_agents_only_summary(
    *,
    run_builtin_checklist: bool,
    agents_run: int,
    findings: int,
) -> str:
    """Build a summary for a run that produced no built-in review summary.

    Args:
        run_builtin_checklist: Whether the built-in checklist pass ran.
        agents_run: Number of custom agent passes that completed.
        findings: Number of findings the custom agents reported.

    Returns:
        A summary sentence, or an empty string when the built-in checklist ran
        and simply produced no summary of its own.
    """
    if run_builtin_checklist:
        return ""
    if agents_run == 0:
        return "No custom review agents matched the changed files."
    return (
        f"Custom review agents only: {agents_run} agent(s) ran and reported "
        f"{findings} finding(s)."
    )


async def _review_chunk(
    *,
    chunk: ReviewChunk,
    context: ReviewContext,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    depth: int,
    checklist_text: str,
    checklist_count: int,
    next_generated_checklist_id: int,
    classifications: list[FileClassification],
    lint_results: str | None,
    budget: CostBudget,
    progress: ReviewProgressCallback | None = None,
    chunk_index: int = 0,
    repo_root: str = "",
    use_one_shot: bool = False,
    strictness_section: str = "",
    diff_budget: int = 0,
    timings: ReviewTimingRecorder | None = None,
) -> tuple[ChunkReviewPartial, int]:
    """Run depth-controlled review for a single chunk.

    Args:
        chunk: The chunk to review.
        context: Collected review diff context.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and fallbacks.
        depth: Review depth level (1-3).
        checklist_text: Pre-formatted checklist prompt text.
        checklist_count: Number of checklist items in the prompt.
        next_generated_checklist_id: First id available to generated items.
        classifications: Domain classifications for changed files.
        lint_results: Optional lint digest for ``--with-lint`` integration.
        budget: Session cost budget tracker.
        progress: Optional progress callback for live status updates.
        chunk_index: Position of the chunk in the run.
        repo_root: Absolute path to the repository under review.
        use_one_shot: When True, avoid durable provider sessions.
        strictness_section: Pre-formatted strictness prompt section.
        diff_budget: Token budget available for embedded diffs.
        timings: Optional recorder for the depth >= 2 question-generation and
            depth >= 3 adversarial spans (#2148). The main provider call is
            timed per chunk by the caller's queued/in-flight split.

    Returns:
        The chunk partial and the next available generated checklist id.
    """
    recorder = timings or ReviewTimingRecorder()
    tracker = progress or NullReviewProgress()
    interaction_paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=chunk.files,
    )
    extra_checklist = ""
    extra_checklist_usage: ChunkReviewPartial | None = None
    if depth >= 2:
        tracker.on_step(chunk_index=chunk_index, step="generating questions")
        with recorder.phase(name=ReviewPhase.GENERATED_QUESTIONS):
            (
                extra_checklist,
                next_generated_checklist_id,
                extra_checklist_usage,
            ) = await generate_extra_checklist(
                chunk=chunk,
                context=context,
                provider=provider,
                ai_config=ai_config,
                budget=budget,
                next_generated_checklist_id=next_generated_checklist_id,
                repo_root=repo_root,
                use_one_shot=use_one_shot,
            )

    tracker.on_step(chunk_index=chunk_index, step="reviewing")
    # Gate before the main provider call so intra-chunk (depth-2/3) work
    # cannot overshoot the budget between the per-chunk checks.
    budget.check()
    findings_cap = resolve_cli_findings_cap(
        transport_is_cli=ai_config.transport == AITransport.CLI,
        cli_max_findings_per_call=ai_config.cli_max_findings_per_call,
    )
    response, elapsed, chunk_degradations = await invoke_chunk_review(
        request=ChunkReviewRequest(
            chunk=chunk,
            context=context,
            provider=provider,
            ai_config=ai_config,
            checklist_text=checklist_text,
            checklist_count=checklist_count,
            interaction_paths=interaction_paths,
            lint_results=lint_results,
            extra_checklist=extra_checklist,
            strictness_section=strictness_section,
            budget=budget,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            diff_budget=diff_budget,
            max_findings=findings_cap,
            chunk_index=chunk_index,
        ),
    )
    response, payload = await parse_review_payload_with_recovery(
        response=response,
        chunk=chunk,
        provider=provider,
        ai_config=ai_config,
        budget=budget,
        repo_root=repo_root,
        use_one_shot=use_one_shot,
        elapsed=elapsed,
    )
    partial = replace(
        payload_to_partial(response=response, payload=payload),
        files=tuple(chunk.files),
        coverage_degradations=chunk_degradations,
    )

    if extra_checklist_usage is not None:
        partial = replace(
            partial,
            input_tokens=partial.input_tokens + extra_checklist_usage.input_tokens,
            output_tokens=partial.output_tokens + extra_checklist_usage.output_tokens,
            cost_estimate=partial.cost_estimate + extra_checklist_usage.cost_estimate,
        )

    if depth >= 3:
        tracker.on_step(chunk_index=chunk_index, step="adversarial sweep")
        with recorder.phase(name=ReviewPhase.ADVERSARIAL):
            adversarial = await run_adversarial_pass(
                chunk=chunk,
                provider=provider,
                ai_config=ai_config,
                prior_findings=partial.findings,
                budget=budget,
                repo_root=repo_root,
                use_one_shot=use_one_shot,
            )
        partial = replace(
            partial,
            findings=merge_findings(
                findings_groups=[partial.findings, adversarial.findings],
            ),
            input_tokens=partial.input_tokens + adversarial.input_tokens,
            output_tokens=partial.output_tokens + adversarial.output_tokens,
            cost_estimate=partial.cost_estimate + adversarial.cost_estimate,
        )

    return partial, next_generated_checklist_id


def _agent_scope_skips(
    *,
    context: ReviewContext,
    selection: CustomAgentSelection,
    run_builtin_checklist: bool,
    completed_agents: frozenset[str],
) -> list[SkippedFile]:
    """List changed files no custom agent reviewed in an agents-only run.

    Under ``review.custom_agents: only`` the built-in checklist never runs, so
    a file no agent looked at is not reviewed at all. Without this record the
    run would report it as reviewed and the gap would read as a clean pass.

    Coverage is credited from the agents that *completed*, never from the ones
    that were merely selected. A selected agent can fail to produce a pass in
    two ways — a non-budget ``AIError`` skips it and the run continues, or a
    cost-cap stop means later agents never start — and in both cases its files
    were scheduled but never read.

    Args:
        context: Collected review context.
        selection: Custom agents partitioned into selected and skipped.
        run_builtin_checklist: Whether the built-in checklist passes ran.
        completed_agents: Names of the agents that returned a completed pass.

    Returns:
        Skip records for the uncovered files; empty when the checklist ran
        (it covers every changed file).
    """
    if run_builtin_checklist:
        return []
    covered = {
        path
        for agent in selection.selected
        if agent.agent.name in completed_agents
        for path in agent.files
    }
    return [
        SkippedFile(path=changed.path, reason=FileSkipReason.AGENT_SCOPE)
        for changed in context.changed_files
        if changed.path not in covered
    ]


def _single_chunk_from_context(*, context: ReviewContext) -> ReviewChunk:
    """Build a single chunk when chunker returns no groups."""
    files = [file.path for file in context.changed_files]
    relationship = REL_SINGLE_FILE if len(files) == 1 else REL_DIRECTORY_PREFIX
    return ReviewChunk(
        id=1,
        files=files,
        diff=context.unified_diff,
        relationship=relationship,
    )


def _empty_review_result(
    *,
    context: ReviewContext,
    provider: BaseAIProvider,
    depth: int,
    checklist_items: list[ChecklistItem],
    context_window_override: int | None,
    context_collection_seconds: float = 0.0,
) -> ReviewResult:
    """Return an empty result when no changes are present."""
    empty_timings = ReviewTimingRecorder()
    empty_timings.add_phase(
        name=ReviewPhase.CONTEXT_COLLECTION,
        seconds=context_collection_seconds,
    )
    context_window = get_context_window(
        model=provider.model_name,
        override=context_window_override,
    )
    metadata = ReviewMetadata(
        model=provider.model_name,
        provider=provider.name,
        context_window=context_window,
        depth=depth,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=len(checklist_items),
        token_usage={"prompt": 0, "completion": 0, "total": 0},
        cost_estimate_usd=0.0,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        timestamp=datetime.now(tz=UTC).isoformat(),
        phase_timings={
            "context_collection": max(context_collection_seconds, 0.0),
            "provider": 0.0,
            "parse_merge": 0.0,
        },
        # Nothing ran after context collection, so the run's duration and
        # the timings total are the same figure (#2148).
        duration_seconds=max(context_collection_seconds, 0.0),
        timings=empty_timings.build(
            total_seconds=max(context_collection_seconds, 0.0),
            max_parallel=1,
        ),
    )
    return ReviewResult(
        metadata=metadata,
        summary="No changes found to review.",
        checklist=(),
        findings=(),
        coverage=CoverageCounts(),
    )
