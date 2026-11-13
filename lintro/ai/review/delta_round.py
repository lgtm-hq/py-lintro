"""Apply a round's delta plan to its chunks and resume plan (#2627, #3403).

The run planner decides *whether* a round reads the delta
(:func:`lintro.ai.review.delta.plan_delta`); this narrows the surviving
chunks to it and records, on the resume plan, what the mid-run checkpoints
and the final match need: the old-side ranges the round showed, and where
each prior open finding sits at the delta's base.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lintro.ai.review.delta import apply_delta_hunks, delta_hunks
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.models.delta_plan import DeltaPlan

if TYPE_CHECKING:
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.resume import ResumePlan

__all__ = ["narrow_round"]


def narrow_round(
    *,
    chunks: list[ReviewChunk],
    delta: DeltaPlan,
    context: ReviewContext,
    repo_root: str,
    resume: ResumePlan,
) -> tuple[list[ReviewChunk], DeltaPlan, ResumePlan]:
    """Narrow a round's chunks to its delta.

    Args:
        chunks: The round's chunks after resume filtering.
        delta: The round's scope decision.
        context: The collected context (head and changed files).
        repo_root: The repository the range is resolved in.
        resume: The round's resume plan.

    Returns:
        The chunks (narrowed where a delta applied), the plan (full with
        :attr:`DeltaReason.DELTA_FAILED` when git could not compute the
        range), and the resume plan carrying the ranges read and the
        relocated finding locations.
    """
    if not delta.is_delta:
        return chunks, delta, resume
    # The mapped locations hold whether or not anything is read: a finding
    # carried unread is still persisted at its mapped location.
    resume = replace(resume, relocations=delta.relocations)
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
