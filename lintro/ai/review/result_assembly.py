"""Turn a finished review run into its :class:`ReviewResult` (issue #2301).

Everything after the last provider call lives here: token and cost totals, the
file-selection and coverage bookkeeping, flag reconciliation, the cross-chunk
guard, and the metadata the CLI, MCP and GitHub surfaces all read. The
orchestrator plans and runs; this module reports.

:class:`~lintro.ai.review.run_planning.ReviewRunPlan` and
:class:`ReviewRunOutcome` are the two halves of the
input: what the run resolved to do, and what it actually did.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.model_pricing import get_context_window
from lintro.ai.review.coverage import (
    inherit_same_round_paths,
    pending_invalidations_for,
)
from lintro.ai.review.coverage_rounds import resolve_run_flags
from lintro.ai.review.diff_gate import DiffGateCounts
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.file_selection import (
    agent_scope_skips,
    resolve_file_selection,
)
from lintro.ai.review.finding_parser import reject_context_findings
from lintro.ai.review.merge import merge_review_results, truncated_paths
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.pr_head import no_tree_degradations
from lintro.ai.review.question_pass import question_pass_degradations
from lintro.ai.review.resume import carried_truncated_paths, records_for_reviewed
from lintro.ai.review.severity_gate import apply_cross_chunk_guard
from lintro.ai.review.synthesis_prompt import guarded_changed_paths
from lintro.ai.review.timings import ReviewPhase, ReviewTimingRecorder
from lintro.ai.review.verification import verification_degradations

if TYPE_CHECKING:
    from lintro.ai.review.custom_agent_runner import CustomAgentPassResult
    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_finding import ReviewFinding
    from lintro.ai.review.models.verification_outcome import VerificationSummary
    from lintro.ai.review.question_pass import RunQuestions
    from lintro.ai.review.run_planning import ReviewRunPlan
    from lintro.ai.review.session import ReviewSessionOptions
    from lintro.ai.review.synthesis import SynthesisPass

__all__ = [
    "ReviewRunOutcome",
    "assemble_review_result",
    "custom_agents_only_summary",
    "empty_review_result",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewRunOutcome:
    """What one review run actually produced, however it ended.

    A run that stopped gracefully (cost cap, timeout, SIGTERM) fills the same
    fields as a completed one, so the report is assembled the same way either
    way; ``stopped_reason`` and ``partial`` are what tell them apart.

    Attributes:
        partials: Completed chunk partials, in chunk order.
        custom_results: Completed custom-agent passes.
        custom_agents_failed: Names of selected agents that produced no pass.
        questions: The once-per-run question pass result (#2720), when run.
        synthesis_pass: The cross-chunk synthesis pass, when one ran (#2269).
        verification: The verification pass's summary, when the run reached
            the completed path (#2728); ``None`` on a stopped run.
        merged: The merged chunk result the report's prose comes from.
        filtered_findings: Findings surviving the sensitivity policy, with the
            custom-agent and synthesis findings already appended.
        custom_findings: The custom-agent subset of ``filtered_findings``.
        total_findings: Count reported to the progress tracker.
        stopped_reason: Empty when the run completed; otherwise the graceful
            stop that ended it.
        partial: True when the run stopped before reviewing every chunk.
        provider_seconds: Wall-clock seconds spent in provider calls.
        parse_merge_seconds: Wall-clock seconds spent parsing and merging.
        validation_started: Monotonic timestamp the validation span opens at.
    """

    partials: list[ChunkReviewPartial] = field(default_factory=list)
    custom_results: list[CustomAgentPassResult] = field(default_factory=list)
    custom_agents_failed: list[str] = field(default_factory=list)
    questions: RunQuestions | None = None
    synthesis_pass: SynthesisPass | None = None
    verification: VerificationSummary | None = None
    merged: ReviewResult = field(
        default_factory=lambda: merge_review_results(partials=[]),
    )
    filtered_findings: tuple[ReviewFinding, ...] = ()
    custom_findings: tuple[ReviewFinding, ...] = ()
    total_findings: int = 0
    stopped_reason: str = ""
    partial: bool = False
    provider_seconds: float = 0.0
    parse_merge_seconds: float = 0.0
    validation_started: float = 0.0


def _run_usage(*, outcome: ReviewRunOutcome) -> tuple[int, int, float]:
    """Sum the tokens and cost of every provider call the run made.

    Args:
        outcome: The run's outcome.

    Returns:
        Input tokens, output tokens and estimated cost.
    """
    usages: list[tuple[int, int, float]] = [
        (item.input_tokens, item.output_tokens, item.cost_estimate)
        for item in outcome.partials
    ]
    usages.extend(
        (result.input_tokens, result.output_tokens, result.cost_estimate)
        for result in outcome.custom_results
    )
    if outcome.synthesis_pass is not None:
        pass_ = outcome.synthesis_pass
        usages.append((pass_.input_tokens, pass_.output_tokens, pass_.cost_estimate))
    if outcome.questions is not None:
        usage = outcome.questions.usage
        usages.append((usage.input_tokens, usage.output_tokens, usage.cost_estimate))
    if outcome.verification is not None:
        check = outcome.verification
        usages.append((check.input_tokens, check.output_tokens, check.cost_estimate))
    return (
        sum(item[0] for item in usages),
        sum(item[1] for item in usages),
        sum(item[2] for item in usages),
    )


def assemble_review_result(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
    plan: ReviewRunPlan,
    outcome: ReviewRunOutcome,
) -> ReviewResult:
    """Build the run's :class:`ReviewResult` from its plan and its outcome.

    Args:
        context: Collected review diff context.
        options: Session options the run was started with.
        plan: What the run resolved to do.
        outcome: What the run produced.

    Returns:
        The complete review result with metadata, checklist, and findings.
    """
    synthesis = outcome.synthesis_pass
    # ``phase_timings`` stays the flat three-key mapping earlier consumers
    # (MCP run payloads, eval stamps) already read; ``timings`` carries the
    # ordered spans and the per-chunk detail.
    phase_timings = {
        "context_collection": max(options.context_collection_seconds, 0.0),
        "provider": max(outcome.provider_seconds, 0.0),
        "parse_merge": max(outcome.parse_merge_seconds, 0.0),
    }

    # Every provider call the run made — chunks, custom agents, synthesis,
    # questions, verification — joins the run totals (#2269, #2720, #2728);
    # charged from the outcome, so a stopped run still reports their cost.
    total_input, total_output, total_cost = _run_usage(outcome=outcome)
    chunks_reviewed = sum(1 for item in outcome.partials if item.files)  # #2731
    # The round's narrative comes from the synthesis pass (lintro-ops
    # milestone 0, decision A); chunks report findings only. A failed or
    # disabled pass leaves both ``None`` and the surfaces render their
    # TL;DR-only fallback.
    pr_summary = synthesis.summary if synthesis is not None else None
    verdict_reasoning = synthesis.verdict_reasoning if synthesis is not None else None
    summary = custom_agents_only_summary(
        run_builtin_checklist=options.run_builtin_checklist,
        agents_run=len(outcome.custom_results),
        findings=len(outcome.custom_findings),
    ) or (pr_summary.headline if pr_summary is not None else "")

    selection = resolve_file_selection(
        context=context,
        chunk_skips=[
            *plan.chunk_skips,
            *agent_scope_skips(
                context=context,
                selection=plan.agent_selection,
                run_builtin_checklist=options.run_builtin_checklist,
                completed_agents=frozenset(
                    result.agent_name for result in outcome.custom_results
                ),
            ),
        ],
    )

    metadata = ReviewMetadata(
        model=options.provider.model_name,
        provider=options.provider.name,
        context_window=plan.context_window,
        depth=options.depth,
        strictness=plan.policy.strictness.value,
        chunks_total=len(plan.chunks),
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
            # #2714: prompt tokens spent on the read-only repository context,
            # a subset of ``prompt`` reported on its own so the before/after
            # measurement can see what the section cost.
            "context": sum(item.context_tokens for item in outcome.partials),
        },
        cost_estimate_usd=total_cost,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        timestamp=datetime.now(tz=UTC).isoformat(),
        token_usage_estimated=options.ai_config.transport == AITransport.CLI,
        partial=outcome.partial,
        chunks_reviewed=chunks_reviewed,
        stopped_reason=outcome.stopped_reason,
        phase_timings=phase_timings,
        custom_agents_run=len(outcome.custom_results),
        diff_gate=sum(
            (item.diff_gate for item in outcome.partials),
            DiffGateCounts(),
        ),
        generated_questions=outcome.questions.lines if outcome.questions else (),
        questions_diff_trimmed=(
            outcome.questions.diff_trimmed if outcome.questions else False
        ),
        custom_agents_skipped=(
            len(plan.agent_selection.skipped) + len(outcome.custom_agents_failed)
        ),
        coverage_degradations=(
            *(
                degradation
                for item in outcome.partials
                for degradation in item.coverage_degradations
            ),
            *(synthesis.degradations if synthesis is not None else ()),
            *question_pass_degradations(questions=outcome.questions),
            *verification_degradations(summary=outcome.verification),
            *no_tree_degradations(plan=plan, ai_config=options.ai_config),
            *(
                CoverageDegradation(
                    reason=CoverageDegradationReason.DIFF_TRUNCATED,
                    chunk_index=CARRIED_CHUNK_INDEX,
                )
                for _path in carried_truncated_paths(
                    plan=plan.resume,
                    prior=None if options.force_full else options.prior_state,
                )
            ),
        ),
        synthesis=synthesis.outcome if synthesis is not None else None,
        verification=outcome.verification,
        lint_facts_note=options.lint_note,
    )

    completed_files = {path for item in outcome.partials for path in item.files}
    agent_files = {path for item in outcome.custom_results for path in item.files}
    actually_reviewed = tuple(
        path
        for path in plan.resume.queue
        if path in completed_files or path in agent_files
    )
    covered_now = inherit_same_round_paths(
        reviewed_now=actually_reviewed,
        eligible_paths=plan.resume.eligible,
        current_hashes=plan.resume.hashes,
    )
    coverage = plan.resume.counts(reviewed_now=covered_now)
    coverage_records = records_for_reviewed(
        plan=plan.resume,
        reviewed_paths=covered_now,
        head_sha=context.head_ref,
        round_number=(
            options.prior_state.next_round if options.prior_state is not None else 1
        ),
        prior=None if options.force_full else options.prior_state,
        stopped_reason=outcome.stopped_reason,
        truncated_paths=truncated_paths(partials=outcome.partials),
    )
    payload_flags = tuple(
        flag for item in outcome.partials for flag in item.flagged_files
    )
    filtered_findings, converted_flags = reject_context_findings(
        findings=outcome.filtered_findings,
        allowed_paths=set(plan.resume.queue),
        eligible_paths=set(plan.resume.eligible),
    )
    # #2265: a chunk only ever sees the other files at the base commit, so a
    # finding asserting that a file this PR changed was never touched is
    # reporting its own blind spot. The guard runs over the run's full changed
    # set, not the chunk's, and downgrades rather than drops.
    filtered_findings = apply_cross_chunk_guard(
        findings=filtered_findings,
        changed_paths=guarded_changed_paths(context=context),
    )
    if synthesis is not None:
        # Both passes above run *after* the synthesis pass returned its own
        # tally, and on a resumed run either can convert or discard one of its
        # findings — ``reject_context_findings`` drops a finding on a path
        # this round was not asked to re-review. The number every surface
        # renders must be the number that actually survived, so it is counted
        # from what is left rather than carried over from the pass.
        metadata = replace(
            metadata,
            synthesis=replace(
                synthesis.outcome,
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
    flagged_files, consumed_flags = resolve_run_flags(
        payload_flags=payload_flags,
        converted=(
            *converted_flags,
            *(f for item in outcome.partials for f in item.converted_flags),
        ),
        prior_flags=prior_flags,
        prior_consumed=prior_consumed,
        covered_now=covered_now,
        current_hashes=plan.resume.hashes,
    )
    awaiting_paths = tuple(
        item.path
        for item in plan.resume.classified
        if item.need is not FileReviewNeed.COVERED and item.path not in covered_now
    )
    # Built once: the membership test below runs per classified file, and a
    # large diff would otherwise rebuild the same set for every one of them.
    awaiting = set(awaiting_paths)
    awaiting_reasons = tuple(
        (item.path, item.flag_reason)
        for item in plan.resume.classified
        if item.path in awaiting and item.flag_reason
    )
    # The validation span (context-finding rejection, coverage and resume
    # bookkeeping, flag reconciliation) and the run total are closed here so
    # the breakdown covers everything the caller waited for (#2148).
    plan.timings.add_phase(
        name=ReviewPhase.VALIDATION,
        seconds=time.monotonic() - outcome.validation_started,
    )
    duration_seconds = time.monotonic() - plan.timings.started_at
    metadata = replace(
        metadata,
        reviewed_paths=actually_reviewed,
        files_reviewed=len(actually_reviewed),
        duration_seconds=duration_seconds,
        timings=plan.timings.build(
            total_seconds=duration_seconds,
            max_parallel=plan.effective_max_parallel,
        ),
    )

    return ReviewResult(
        metadata=metadata,
        summary=summary,
        findings=filtered_findings,
        pr_summary=pr_summary,
        verdict_reasoning=verdict_reasoning,
        coverage=coverage,
        coverage_records=coverage_records,
        flagged_files=flagged_files,
        awaiting_paths=awaiting_paths,
        awaiting_reasons=awaiting_reasons,
        pending_invalidations=pending_invalidations_for(
            classified=plan.resume.classified,
            reviewed_now=covered_now,
        ),
        consumed_flags=consumed_flags,
    )


def custom_agents_only_summary(
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


def empty_review_result(
    *,
    context: ReviewContext,
    options: ReviewSessionOptions,
) -> ReviewResult:
    """Return an empty result when no changes are present.

    Args:
        context: Collected review diff context.
        options: Session options for the run.

    Returns:
        A result recording that the review had nothing to look at.
    """
    provider = options.provider
    depth = options.depth
    checklist_items = options.checklist_items
    context_window_override = options.context_window_override
    context_collection_seconds = options.context_collection_seconds
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
        lint_facts_note=options.lint_note,
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
        findings=(),
        coverage=CoverageCounts(),
    )
