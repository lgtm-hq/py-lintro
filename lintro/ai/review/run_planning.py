"""Resolve what one review run will do, before its first provider call.

Everything the run decides up front lives here: the sensitivity policy, the
diff-token budget (including the CLI transport's tighter ceiling), the chunk
plan, the resume plan, the custom-agent selection, and the concurrency ceiling
a cost cap forces down. :func:`plan_run` returns all of it as one frozen
:class:`ReviewRunPlan`, which the orchestrator hands to the executor and then
to result assembly — so the run's decisions are made once and read, never
re-derived.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.budget import CostBudget
from lintro.ai.enums import AITransport
from lintro.ai.model_pricing import (
    calculate_available_diff_tokens,
    get_context_window,
)
from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.cli_limits import (
    assert_cli_diff_within_ceiling,
    resolve_cli_diff_budget,
)
from lintro.ai.review.custom_agents import select_custom_agents
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.group_labels import REL_DIRECTORY_PREFIX, REL_SINGLE_FILE
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.progress import NullReviewProgress
from lintro.ai.review.prompts import estimate_prompt_overhead
from lintro.ai.review.resume import filter_chunks, plan_resume
from lintro.ai.review.sensitivity import (
    ReviewSensitivityPolicy,
    format_strictness_prompt_section,
)
from lintro.ai.review.timings import ReviewPhase
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.review.custom_agents import CustomAgentSelection
    from lintro.ai.review.models.file_classification import FileClassification
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.skipped_file import SkippedFile
    from lintro.ai.review.progress import ReviewProgressCallback
    from lintro.ai.review.resume import ResumePlan
    from lintro.ai.review.session import ReviewSessionOptions
    from lintro.ai.review.timings import ReviewTimingRecorder

__all__ = ["ReviewRunPlan", "plan_run", "resolve_review_chunks"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewRunPlan:
    """What one review run resolved to do, before any provider call.

    Attributes:
        policy: Sensitivity policy resolved for the run.
        strictness_section: Pre-formatted strictness prompt section.
        context_window: Context window resolved for the provider model.
        diff_budget: Token budget available for embedded diffs.
        chunks: The chunks the run will review, in plan order.
        chunk_skips: Per-file skips the chunker recorded.
        resume: Resume plan for the current diff.
        agent_selection: Custom agents partitioned into selected and skipped.
        ai_config: AI configuration after any per-run timeout override.
        tracker: Progress callback for live status updates.
        budget: Run cost budget tracker.
        max_parallel_calls: Concurrency ceiling passed to the chunk fan-out.
        effective_max_parallel: The ceiling actually reachable for this run,
            reported in the timings breakdown.
        use_durable_session: Whether the provider opens a durable session.
        repo_root: Absolute path to the repository under review.
        use_one_shot: When True, chunk calls avoid durable provider sessions.
        timings: Recorder for the run's phase and per-chunk spans (#2148).
    """

    policy: ReviewSensitivityPolicy
    strictness_section: str
    context_window: int
    diff_budget: int
    chunks: list[ReviewChunk]
    chunk_skips: list[SkippedFile]
    resume: ResumePlan
    agent_selection: CustomAgentSelection
    ai_config: AIConfig
    tracker: ReviewProgressCallback
    budget: CostBudget
    max_parallel_calls: int
    effective_max_parallel: int
    use_durable_session: bool
    repo_root: str
    use_one_shot: bool
    timings: ReviewTimingRecorder


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


def _resolve_diff_budget(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    context_window: int,
) -> int:
    """Resolve how many tokens of diff one provider call may carry.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        context_window: Context window resolved for the provider model.

    Returns:
        The token budget available for embedded diffs.
    """
    diff_budget = calculate_available_diff_tokens(
        context_window=context_window,
        prompt_overhead=estimate_prompt_overhead(
            context=context,
            checklist_text=options.checklist_text,
            classifications=options.classifications,
            lint_results=options.lint_results,
        ),
    )
    if options.ai_config.transport != AITransport.CLI:
        return diff_budget
    # Context-window budgets are transport-blind and leave ~1.5k-line PRs
    # as a single CLI chunk (#1967). Tighten before the chunker runs, and
    # refuse outright when the full diff exceeds the hard ceiling.
    assert_cli_diff_within_ceiling(
        context=context,
        cli_max_diff_bytes=options.ai_config.cli_max_diff_bytes,
    )
    return resolve_cli_diff_budget(
        context_window_budget=diff_budget,
        cli_max_diff_tokens=options.ai_config.cli_max_diff_tokens,
    )


def plan_run(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    timings: ReviewTimingRecorder,
) -> ReviewRunPlan:
    """Resolve everything the run needs before its first provider call.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        timings: Recorder the chunking and resume-planning spans open on.

    Returns:
        The resolved plan for the run.
    """
    policy = options.sensitivity or ReviewSensitivityPolicy(
        strictness=ReviewStrictness.BALANCED,
        report_migration_notes=True,
        report_doc_drift=True,
        report_test_gaps=True,
    )
    context_window = get_context_window(
        model=options.provider.model_name,
        override=options.context_window_override,
    )
    diff_budget = _resolve_diff_budget(
        context=context,
        options=options,
        context_window=context_window,
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
    # A cost cap serializes chunk calls so the resume queue cannot invert
    # (#2154); the effective ceiling is reported alongside the timings so a
    # slow run's concurrency is never guessed at.
    max_parallel_calls = (
        1
        if options.enforce_cost_cap and options.ai_config.max_cost_usd is not None
        else options.ai_config.max_parallel_calls
    )
    return ReviewRunPlan(
        policy=policy,
        strictness_section=format_strictness_prompt_section(policy=policy),
        context_window=context_window,
        diff_budget=diff_budget,
        chunks=chunks,
        chunk_skips=chunk_skips,
        resume=resume,
        agent_selection=agent_selection,
        ai_config=(
            options.ai_config.model_copy(update={"api_timeout": options.timeout})
            if options.timeout is not None
            else options.ai_config
        ),
        tracker=options.progress or NullReviewProgress(),
        budget=CostBudget(
            max_cost_usd=(
                options.ai_config.max_cost_usd if options.enforce_cost_cap else None
            ),
        ),
        max_parallel_calls=max_parallel_calls,
        effective_max_parallel=max(min(len(chunks), max_parallel_calls), 1),
        # Branch on the provider's declared capability, not its identity
        # (#1241): a durable session only helps when the transport can resume
        # one. begin/end_durable_session are concrete no-ops on
        # BaseAIProvider, so no hasattr guard is needed -- every provider
        # answers them.
        use_durable_session=(
            options.provider.capabilities.supports_sessions and len(chunks) == 1
        ),
        repo_root=context.repo_root or os.getcwd(),
        use_one_shot=len(chunks) > 1,
        timings=timings,
    )
