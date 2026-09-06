"""Mid-run coverage checkpoints for the review (issue #2301).

CI runners can send SIGTERM long before a large review finishes. The chunk
fan-out calls :func:`write_incremental_coverage_part` after every completed
chunk so the work already done is on disk when that happens, and the next run
resumes from it instead of re-reviewing the whole diff.

The checkpoint is written only when ``LINTRO_REVIEW_STATE_DIR`` is set, so a
local review never touches the state directory.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.coverage import inherit_same_round_paths
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.resume import records_for_reviewed
from lintro.ai.review.sensitivity import filter_findings_by_policy
from lintro.ai.review.state_store import state_dir, write_state_part

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.resume import ResumePlan
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy

__all__ = ["checkpoint_writer", "write_incremental_coverage_part"]


def write_incremental_coverage_part(
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


def checkpoint_writer(
    *,
    resume: ResumePlan,
    context: ReviewContext,
    prior_state: ReviewState | None,
    force_full: bool,
    policy: ReviewSensitivityPolicy,
) -> Callable[[list[ChunkReviewPartial]], None]:
    """Build the per-chunk callback that writes the run's coverage parts.

    Parts are numbered monotonically, and a part that fails to write is logged
    and skipped without advancing the sequence: a checkpoint is best-effort, so
    a broken state directory must never fail the review it is protecting.

    Args:
        resume: Resume plan for the current diff.
        context: Review diff context (head SHA).
        prior_state: Prior artifact state, if any.
        force_full: When True, do not inherit prior coverage.
        policy: Sensitivity policy used to filter checkpoint findings.

    Returns:
        A callback the chunk fan-out invokes with everything completed so far.
    """
    sequence = 0

    def _checkpoint(collected: list[ChunkReviewPartial]) -> None:
        """Write an incremental coverage part after each finished chunk.

        Args:
            collected: Chunk partials completed so far in this run.
        """
        nonlocal sequence
        next_sequence = sequence + 1
        try:
            write_incremental_coverage_part(
                collected=collected,
                resume=resume,
                context=context,
                prior_state=prior_state,
                force_full=force_full,
                sequence=next_sequence,
                policy=policy,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "Could not write incremental review-resume part {n}",
                n=next_sequence,
            )
        else:
            sequence = next_sequence

    return _checkpoint
