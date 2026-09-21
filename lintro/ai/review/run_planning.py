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
    resolve_chunk_diff_budget,
    resolve_synthesis_diff_budget,
)
from lintro.ai.review.custom_agents import select_custom_agents
from lintro.ai.review.enums.review_checkout import ReviewCheckout
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
        synthesis_diff_budget: Input token budget of the cross-chunk
            synthesis pass (``ai.review_synthesis_diff_tokens`` clamped to
            the context-window remainder, #2702).
        context_budget: Effective per-chunk budget of the read-only repository
            context section, clamped to the window remainder (#2714).
        diff_ceiling: The context-window remainder one chunk's diff may fill;
            the context section takes only what a chunk's own diff leaves.
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
        tools_disabled: True when the run has no tree the agent may read
            (#2733, ``checkout`` is ``NONE``): every CLI call goes out
            without tools rather than against the ambient working tree.
        timings: Recorder for the run's phase and per-chunk spans (#2148).
    """

    policy: ReviewSensitivityPolicy
    strictness_section: str
    context_window: int
    diff_budget: int
    synthesis_diff_budget: int
    context_budget: int = 0
    diff_ceiling: int = 0
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
    tools_disabled: bool = False
    timings: ReviewTimingRecorder


def resolve_review_chunks(
    *,
    context: ReviewContext,
    diff_budget: int,
    classifications: list[FileClassification],
    force_semantic_chunking: bool = False,
    skipped_sink: list[SkippedFile] | None = None,
    hard_diff_ceiling: int | None = None,
) -> list[ReviewChunk]:
    """Resolve review chunks using a budget-gated fast path.

    When the full diff fits within the token budget, return a single chunk
    without semantic splitting. Otherwise delegate to the semantic chunker.

    Args:
        context: Collected review diff context.
        diff_budget: Per-chunk token target for diff content.
        classifications: Domain classifications for changed files.
        force_semantic_chunking: When True, skip the single-chunk fast path.
        skipped_sink: Optional list the chunker's per-file skips are appended
            to, so the caller can report *why* a changed file went unreviewed
            instead of only how many did (#1910).
        hard_diff_ceiling: Absolute per-chunk ceiling (the context-window
            remainder) a single over-target file may fill whole before it is
            truncated; ``None`` makes the target the ceiling.

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
        hard_max_tokens=hard_diff_ceiling,
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
    """Resolve the per-chunk diff token target for one provider call.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        context_window: Context window resolved for the provider model.

    Returns:
        The per-chunk token target (the smaller of the chunk budget and the
        context-window remainder).
    """
    return _resolve_diff_budgets(
        context=context,
        options=options,
        context_window=context_window,
    )[0]


def _resolve_diff_budgets(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    context_window: int,
) -> tuple[int, int]:
    """Resolve the per-chunk target and the hard per-chunk ceiling.

    Args:
        context: Collected review diff context.
        options: Session options for the run.
        context_window: Context window resolved for the provider model.

    Returns:
        ``(target, ceiling)``: the per-chunk token target the chunker splits
        groups against, and the context-window remainder a single file may
        fill before it has to be truncated.
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
    if options.ai_config.transport == AITransport.CLI:
        # The CLI transport spawns one process per chunk, so a diff above the
        # hard byte ceiling is refused outright rather than fanned out into
        # an unbounded number of chunks (#1967).
        assert_cli_diff_within_ceiling(
            context=context,
            cli_max_diff_bytes=options.ai_config.cli_max_diff_bytes,
        )
    # Context-window budgets are transport-blind and leave most PRs as one
    # slow chunk. The per-chunk budget applies on every transport so the
    # chunker produces small file-group chunks that review at depth and run
    # in parallel (lintro-ops milestone 0, decision A).
    target = resolve_chunk_diff_budget(
        context_window_budget=diff_budget,
        review_chunk_diff_tokens=options.ai_config.review_chunk_diff_tokens,
    )
    return target, max(diff_budget, target)


def resolve_context_budget(
    *,
    review_context_tokens: int,
    window_remainder: int,
    diff_target: int,
) -> int:
    """Clamp the repository-context budget to what the window leaves (#2714).

    The context section rides in the same prompt as the chunk diff, so it may
    only take what the context window leaves after the prompt overhead and
    the per-chunk diff target; otherwise a correctly chunked prompt could
    overrun the provider's limit.

    Args:
        review_context_tokens: The configured per-chunk context budget.
        window_remainder: Tokens the window leaves for diff plus context.
        diff_target: The per-chunk diff target the chunker packs against.

    Returns:
        The effective context budget, ``0`` when nothing is left.
    """
    return max(min(review_context_tokens, window_remainder - diff_target), 0)


#: Concurrency ceiling on the CLI transport when ``ai.max_parallel_calls`` is
#: left at its default. A CLI call spawns a whole agent process, so five in
#: flight thrash a laptop and trip provider rate limits where five API
#: calls do not (lintro-ops #37).
CLI_DEFAULT_MAX_PARALLEL_CALLS: int = 3


def resolve_max_parallel_calls(
    *,
    ai_config: AIConfig,
    enforce_cost_cap: bool,
) -> int:
    """Return the concurrency ceiling for this run's chunk fan-out.

    A cost cap serializes chunk calls so the resume queue cannot invert
    (#2154). Otherwise the CLI transport is clamped to
    :data:`CLI_DEFAULT_MAX_PARALLEL_CALLS` unless ``ai.max_parallel_calls``
    was set explicitly, in which case the user's number wins on every
    transport. The effective ceiling is reported alongside the timings so a
    slow run's concurrency is never guessed at.

    Args:
        ai_config: Resolved AI configuration for the run.
        enforce_cost_cap: Whether the run enforces ``ai.max_cost_usd``.

    Returns:
        Positive concurrency ceiling.
    """
    if enforce_cost_cap and ai_config.max_cost_usd is not None:
        return 1
    if (
        ai_config.transport == AITransport.CLI
        and "max_parallel_calls" not in ai_config.model_fields_set
    ):
        return min(ai_config.max_parallel_calls, CLI_DEFAULT_MAX_PARALLEL_CALLS)
    return ai_config.max_parallel_calls


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
    diff_budget, hard_diff_ceiling = _resolve_diff_budgets(
        context=context,
        options=options,
        context_window=context_window,
    )
    context_budget = resolve_context_budget(
        review_context_tokens=options.ai_config.review_context_tokens,
        window_remainder=hard_diff_ceiling,
        diff_target=diff_budget,
    )
    synthesis_diff_budget = resolve_synthesis_diff_budget(
        context_window_budget=hard_diff_ceiling,
        review_synthesis_diff_tokens=options.ai_config.review_synthesis_diff_tokens,
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
                hard_diff_ceiling=hard_diff_ceiling,
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
    max_parallel_calls = resolve_max_parallel_calls(
        ai_config=options.ai_config,
        enforce_cost_cap=options.enforce_cost_cap,
    )
    return ReviewRunPlan(
        policy=policy,
        strictness_section=format_strictness_prompt_section(policy=policy),
        context_window=context_window,
        diff_budget=diff_budget,
        synthesis_diff_budget=synthesis_diff_budget,
        context_budget=context_budget,
        diff_ceiling=hard_diff_ceiling,
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
        tools_disabled=context.checkout is ReviewCheckout.NONE,
        timings=timings,
    )
