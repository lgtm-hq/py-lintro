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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.cost import estimate_cost
from lintro.ai.enums.cost_basis import SHADOW_COST_BASES, CostBasis
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.review.coverage import inherit_same_round_paths
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_constants import MAX_STORED_RUNS
from lintro.ai.review.merge import truncated_paths
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.resume import records_for_reviewed
from lintro.ai.review.reuse_gate import INHERITED_EVIDENCE_STATES
from lintro.ai.review.sensitivity import filter_findings_by_policy
from lintro.ai.review.severity_gate import apply_severity_gates
from lintro.ai.review.state_store import state_dir, write_state_part
from lintro.ai.review.sticky.state import ranges_by_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.resume import ResumePlan
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy

__all__ = ["CheckpointStamp", "checkpoint_writer", "write_incremental_coverage_part"]


@dataclass(frozen=True, slots=True)
class CheckpointStamp:
    """What a checkpoint stamps beyond the run's own coverage and findings.

    Attributes:
        pr_number: The PR the final write keys this run's state on (the
            CLI's ``state_pr``: ``--pr`` or the CI event, #2814). None falls
            back to ``PR_NUMBER``, then to the prior state's PR.
        round_spend_usd: What this round has spent so far (#2796).
        identity: The round's identity, less its number (#2867): written as
            a partial run record so a round killed after this checkpoint
            leaves coverage the reuse gate can place on a lane and endpoint.
            ``None`` writes no run record.
        cost_basis: The basis the transport profile resolved for the round.
            On ``unknown`` the checkpointed figure is a shadow, not money,
            and it is the finished chunks' token counts priced on
            ``shadow_price_model``, never the envelope's figure.
        shadow_price_model: The model whose list table prices a shadow.
        verify: Reads what the calls completed so far established: the
            verification state, the reported models and whether a cost
            figure was reported, so the partial record carries them and a
            subscription figure is booked as the shadow it is; ``None``
            records none of it.
    """

    pr_number: int | None = None
    round_spend_usd: float = 0.0
    identity: RunIdentity | None = None
    cost_basis: str = ""
    shadow_price_model: str = ""
    verify: Callable[[], LedgerSnapshot] | None = None


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """What the open ledger had established when a checkpoint was written.

    Attributes:
        state: The verification state over the calls completed so far, or
            empty when none completed.
        reported_models: The models those calls reported.
        cost_reported: Whether any of them reported a cost figure.
    """

    state: str = ""
    reported_models: tuple[ReportedModel, ...] = ()
    cost_reported: bool = False


_NO_STAMP = CheckpointStamp()


def write_incremental_coverage_part(
    *,
    collected: list[ChunkReviewPartial],
    resume: ResumePlan,
    context: ReviewContext,
    prior_state: ReviewState | None,
    force_full: bool,
    sequence: int,
    policy: ReviewSensitivityPolicy,
    stamp: CheckpointStamp = _NO_STAMP,
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
        stamp: The PR this run's state is keyed on, and what the round has
            spent so far (persisted on top of the prior total, so a round
            killed after this checkpoint still counts against the budget).
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
    round_number = prior_state.next_round if prior_state is not None else 1
    records = records_for_reviewed(
        plan=resume,
        reviewed_paths=covered_now,
        head_sha=context.head_ref,
        round_number=round_number,
        prior=None if force_full else prior_state,
        truncated_paths=truncated_paths(partials=collected),
    )
    pr_raw = os.environ.get("PR_NUMBER", "").strip()
    seed = ReviewState() if force_full or prior_state is None else prior_state
    # The chunk passes parse ungated since #2728 (the round gates once, after
    # the verification pass). A checkpoint written on an interrupt never
    # reaches that round, so it gates here or an inflated P1 would be
    # persisted at the model's severity and replayed on resume.
    findings = filter_findings_by_policy(
        findings=apply_severity_gates(
            findings=tuple(
                finding for partial in collected for finding in partial.findings
            ),
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
        # A delta round (#2627) resolves only what it re-read, mid-run too.
        reviewed_ranges=ranges_by_path(reviewed_ranges=resume.reviewed_ranges),
    )
    # A shadow figure is never spend (#2867, ADR-0011 B): a gateway's is
    # the finished chunks' tokens on the shadow table, and a subscription
    # figure the envelope reported is the list-price shadow. Only a
    # subscription round that reported no figure keeps its local estimate
    # as spend, as its final record will.
    snapshot = stamp.verify() if stamp.verify is not None else LedgerSnapshot()
    basis, round_figure = _checkpoint_figure(
        collected=collected,
        stamp=stamp,
        snapshot=snapshot,
    )
    shadow_basis = basis in {value.value for value in SHADOW_COST_BASES}
    prior_spend = prior_state.review_spend_usd if prior_state is not None else 0.0
    prior_shadow = prior_state.review_shadow_usd if prior_state is not None else 0.0
    runs, truncated = _runs_with_checkpoint(
        # History, not coverage: a ``--full`` round keeps earlier rounds.
        history=prior_state.runs if prior_state is not None else (),
        stamp=stamp,
        snapshot=snapshot,
        round_number=round_number,
        basis=basis,
        round_figure=round_figure,
        shadow_basis=shadow_basis,
    )
    write_state_part(
        state=replace(
            seed,
            runs=runs,
            truncated=seed.truncated or truncated,
            findings=match.records,
            coverage=records,
            repo=os.environ.get("GITHUB_REPOSITORY", "") or seed.repo,
            # The same PR the final write keys on, so a named-PR resume load
            # never skips this run's own parts (#2814).
            pr_number=_checkpoint_pr(stamp=stamp, pr_raw=pr_raw, seed=seed),
            base_sha=context.base_ref or seed.base_sha,
            head_sha=context.head_ref or seed.head_sha,
            workflow="ai-review.yml",
            event=os.environ.get("GITHUB_EVENT_NAME", "") or seed.event,
            run_id=os.environ.get("GITHUB_RUN_ID", "") or seed.run_id,
            # From the prior state, not ``seed``: a ``--full`` seed is empty,
            # and a full round must not reset the PR's spend.
            pr_spend_usd=prior_spend + (0.0 if shadow_basis else round_figure),
            pr_shadow_usd=prior_shadow + (round_figure if shadow_basis else 0.0),
        ),
        directory=state_dir(ci=True),
        sequence=sequence,
        final=True,
    )


def _checkpoint_figure(
    *,
    collected: list[ChunkReviewPartial],
    stamp: CheckpointStamp,
    snapshot: LedgerSnapshot,
) -> tuple[str, float]:
    """Resolve the basis and figure a checkpoint books for the round so far.

    Args:
        collected: Chunks finished so far in this run.
        stamp: The checkpoint stamp with the profile's basis.
        snapshot: What the open ledger established so far.

    Returns:
        The basis the partial record carries and the figure it books.
    """
    if stamp.cost_basis == CostBasis.UNKNOWN.value:
        return CostBasis.UNKNOWN.value, _token_priced_shadow(
            collected=collected,
            stamp=stamp,
        )
    if stamp.cost_basis == CostBasis.UNPRICEABLE.value and snapshot.cost_reported:
        return CostBasis.LIST_PRICE_SHADOW.value, stamp.round_spend_usd
    return stamp.cost_basis, stamp.round_spend_usd


def _token_priced_shadow(
    *,
    collected: list[ChunkReviewPartial],
    stamp: CheckpointStamp,
) -> float:
    """Price the finished chunks' tokens on the shadow model (ADR-0011 B).

    Args:
        collected: Chunks finished so far in this run.
        stamp: The checkpoint stamp naming the shadow price model.

    Returns:
        The shadow figure for the round so far.
    """
    model = stamp.shadow_price_model or (
        stamp.identity.model if stamp.identity is not None else ""
    )
    return estimate_cost(
        model,
        sum(partial.input_tokens for partial in collected),
        sum(partial.output_tokens for partial in collected),
    )


def _runs_with_checkpoint(
    *,
    history: tuple[RunRecord, ...],
    stamp: CheckpointStamp,
    snapshot: LedgerSnapshot,
    round_number: int,
    basis: str,
    round_figure: float,
    shadow_basis: bool,
) -> tuple[tuple[RunRecord, ...], bool]:
    """Return the stored history plus this round's checkpoint record, when stamped.

    Args:
        history: The runs stored before this round.
        stamp: The checkpoint stamp; ``identity`` may be ``None``.
        snapshot: What the open ledger established so far.
        round_number: The round being checkpointed.
        basis: The basis the partial record carries.
        round_figure: What the round has spent, or its shadow, so far.
        shadow_basis: Whether the round's figure is a shadow.

    Returns:
        The runs to persist, capped like the sticky's history, and whether
        the cap dropped any. Without an identity the history is kept as it
        is.
    """
    if stamp.identity is None:
        return history, False
    # An earlier attempt at this round whose coverage this one may have
    # inherited keeps its model evidence on the record (#2867).
    seen = {model.name: model for model in snapshot.reported_models}
    for run in history:
        if (
            run.coverage.checkpoint
            and run.identity.round == round_number
            and run.identity.model_verification in INHERITED_EVIDENCE_STATES
        ):
            for model in run.identity.reported_models:
                seen.setdefault(model.name, model)
    partial = RunRecord(
        identity=replace(
            stamp.identity,
            round=round_number,
            model_verification=snapshot.state,
            reported_models=tuple(seen.values()),
        ),
        coverage=RunCoverage(partial=True, checkpoint=True),
        usage=RunUsage(
            cost=0.0 if shadow_basis else round_figure,
            cost_shadow=round_figure if shadow_basis else 0.0,
            cost_basis=basis,
        ),
        outcome=RunOutcome(verdict=ReviewVerdict.INCOMPLETE),
    )
    combined = [
        *(run for run in history if run.identity.round != round_number),
        partial,
    ]
    return tuple(combined[-MAX_STORED_RUNS:]), len(combined) > MAX_STORED_RUNS


def _checkpoint_pr(
    *,
    stamp: CheckpointStamp,
    pr_raw: str,
    seed: ReviewState,
) -> int | None:
    """Return the PR a checkpoint is stamped with.

    Args:
        stamp: The caller's stamp; its ``pr_number`` wins when set.
        pr_raw: ``PR_NUMBER`` from the environment, stripped.
        seed: The state the checkpoint is built on.

    Returns:
        The PR number, or None when nothing names one.
    """
    if stamp.pr_number is not None:
        return stamp.pr_number
    return int(pr_raw) if pr_raw.isdigit() else seed.pr_number


def checkpoint_writer(
    *,
    resume: ResumePlan,
    context: ReviewContext,
    prior_state: ReviewState | None,
    force_full: bool,
    policy: ReviewSensitivityPolicy,
    round_spend: Callable[[], float] | None = None,
    state_pr: int | None = None,
    stamp: CheckpointStamp = _NO_STAMP,
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
        round_spend: Reads what the round has spent so far (its
            ``CostBudget.spent``), or None to record no spend.
        state_pr: The PR the final write keys this run's state on, or None.
        stamp: The round's identity for the partial run record each
            checkpoint writes and the round's cost basis (#2867). Its
            ``pr_number`` and ``round_spend_usd`` are filled from
            ``state_pr`` and ``round_spend`` at each checkpoint.

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
                stamp=replace(
                    stamp,
                    pr_number=state_pr,
                    round_spend_usd=round_spend() if round_spend is not None else 0.0,
                ),
            )
        except Exception:
            logger.opt(exception=True).warning(
                "Could not write incremental review-resume part {n}",
                n=next_sequence,
            )
        else:
            sequence = next_sequence

    return _checkpoint
