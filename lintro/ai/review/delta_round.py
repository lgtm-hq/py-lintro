"""Apply a round's delta plan to its chunks and resume plan (#2627, #3403).

The run planner decides *whether* a round reads the delta
(:func:`lintro.ai.review.delta.plan_delta`); this narrows the surviving
chunks to it and records, on the resume plan, what the mid-run checkpoints
and the final match need: the old-side ranges the round showed, and where
each prior open finding sits at the delta's base.

A full round narrows nothing, but its prior open findings still move with
their files (#3413): each is placed at this round's head through the same
git mapping, so a finding whose file was renamed pairs under its new path,
keeping its key and its thread, whatever scope the round read.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.base_move_hold import hold_across_base_moves
from lintro.ai.review.delta import apply_delta_hunks, delta_hunks
from lintro.ai.review.delta_lines import map_open_findings, pr_renames
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.models.delta_plan import DeltaPlan

if TYPE_CHECKING:
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.resume import ResumePlan

__all__ = ["narrow_round"]


def narrow_round(
    *,
    chunks: list[ReviewChunk],
    delta: DeltaPlan,
    context: ReviewContext,
    repo_root: str,
    resume: ResumePlan,
    prior: ReviewState | None = None,
) -> tuple[list[ReviewChunk], DeltaPlan, ResumePlan]:
    """Narrow a round's chunks to its delta.

    Args:
        chunks: The round's chunks after resume filtering.
        delta: The round's scope decision.
        context: The collected context (head and changed files).
        repo_root: The repository the range is resolved in.
        resume: The round's resume plan.
        prior: State of the previous rounds; on a full round its open
            findings are placed at this head (#3413).

    Returns:
        The chunks (narrowed where a delta applied), the plan (full with
        :attr:`DeltaReason.DELTA_FAILED` when git could not compute the
        range), and the resume plan carrying the ranges read and the
        relocated finding locations.
    """
    if not delta.is_delta:
        return (
            chunks,
            delta,
            _place_at_head(
                prior=prior,
                context=context,
                repo_root=repo_root,
                resume=resume,
            ),
        )
    # The mapped locations hold whether or not anything is read: a finding
    # carried unread is still persisted at its mapped location. One a moved
    # base pushed out of the diff is held instead (#3467).
    resume = replace(
        resume,
        relocations=hold_across_base_moves(
            prior=prior,
            context=context,
            repo_root=repo_root,
            relocations=delta.relocations,
            at_head=False,
        ),
    )
    if not chunks:
        return chunks, delta, resume
    hunks = delta_hunks(
        repo_root=repo_root,
        since_sha=delta.since_sha or "",
        head_sha=context.head_ref,
        pr_paths=(file.path for file in context.changed_files),
    )
    if hunks is None:
        return chunks, DeltaPlan.full(reason=DeltaReason.DELTA_FAILED), resume
    applied = apply_delta_hunks(
        chunks=chunks,
        hunks=hunks,
        since_sha=delta.since_sha or "",
        open_lines=delta.open_lines,
    )
    # The checkpoints match on the same ranges as the final round.
    return (
        applied.chunks,
        delta,
        replace(resume, reviewed_ranges=applied.reviewed_ranges),
    )


def _place_at_head(
    *,
    prior: ReviewState | None,
    context: ReviewContext,
    repo_root: str,
    resume: ResumePlan,
) -> ResumePlan:
    """Record where a full round's prior open findings sit at its head (#3413).

    A full round reads every file whole, so no line needs showing; only the
    relocations matter. Mapping to the head itself follows every rename since
    the head that last reported each finding, chained renames included.

    Args:
        prior: State of the previous rounds, or ``None``.
        context: The collected context (head and checkout).
        repo_root: The repository the head is resolved in.
        resume: The round's resume plan.

    Returns:
        The resume plan carrying the relocations; unchanged on a run that is
        not a pull request (as :func:`lintro.ai.review.delta.plan_delta`
        decides) and without prior state, a head, or a tree to ask git about.
    """
    head_sha = context.head_ref
    if (
        prior is None
        or context.pr_metadata is None
        or not head_sha
        or context.checkout is ReviewCheckout.NONE
    ):
        return resume
    mapped = map_open_findings(
        prior=prior,
        since_sha=head_sha,
        head_sha=head_sha,
        repo_root=repo_root,
        renamed_from=pr_renames(changed_files=context.changed_files),
    )
    relocations = hold_across_base_moves(
        prior=prior,
        context=context,
        repo_root=repo_root,
        relocations=mapped.relocations,
        at_head=True,
    )
    if not relocations:
        return resume
    return replace(resume, relocations=relocations)
