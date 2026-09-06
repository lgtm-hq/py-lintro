"""Public entry points for the sticky comment.

One round of a review produces one matching outcome, one verdict and one run
record. Both the comment and the state persisted alongside it are derived from
that single :class:`~lintro.ai.review.models.round_outcome.RoundOutcome`, so a
persist path and the board that announces it cannot disagree about what the
round did.

Sizing is not decided here either: the per-limits renders below are handed to
``github_contract.fit_body``, which owns the pruning order and the final cap
for every posting path (#2303).
"""

from __future__ import annotations

from dataclasses import replace

from lintro.ai.review.convergence import score_records
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.finding_matcher import derive_verdict, match_findings
from lintro.ai.review.github_constants import (
    MAX_COMMENT_CHARS,
    MAX_STORED_RUNS,
    PRIMARY_SOFT_LIMIT,
    STATE_VERSION,
    STICKY_FOOTER,
    STICKY_MARKER,
)
from lintro.ai.review.github_contract import (
    RenderLimits,
    SectionCounts,
    cap_body,
    fit_body,
)
from lintro.ai.review.github_render import Section, assemble
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.round_outcome import RoundOutcome
from lintro.ai.review.models.sticky_plan import StickyPlan
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky.body import round_sections, state_sections
from lintro.ai.review.sticky.history import _archive_body
from lintro.ai.review.sticky.state import (
    _run_record,
    matcher_reviewed_paths,
    stamp_comment_ids,
)
from lintro.ai.review.verdict import apply_coverage_gate

__all__ = [
    "advance_review_state",
    "build_sticky_bodies",
    "build_sticky_comment",
    "render_state_sticky",
]


def build_sticky_comment(*, request: StickyRequest) -> str:
    """Compose the primary sticky body.

    Args:
        request: Inputs for this round. See :func:`build_sticky_bodies`.

    Returns:
        The primary sticky body, without the archive comment.
    """
    primary, _archive = build_sticky_bodies(request=request)
    return primary


def _round_outcome(*, request: StickyRequest) -> RoundOutcome:
    """Decide what this round found, concluded and recorded.

    Args:
        request: Inputs for this round.

    Returns:
        RoundOutcome: The matching outcome, verdict and run record every
        consumer of this round reads from.
    """
    state = request.prior_state or ReviewState()
    round_number = state.next_round
    match = match_findings(
        previous=state,
        findings=request.result.findings,
        round_number=round_number,
        head_sha=request.head_sha,
        reviewed_paths=matcher_reviewed_paths(result=request.result),
        departed_paths=request.departed_paths,
    )
    match = replace(
        match,
        records=stamp_comment_ids(
            records=match.records,
            comment_ids=request.inline_comment_ids,
        ),
    )
    findings_verdict = derive_verdict(findings=match.records)
    if request.result.coverage is not None:
        verdict = apply_coverage_gate(
            findings_verdict=findings_verdict,
            coverage_complete=request.result.coverage.complete,
        )
    else:
        verdict = findings_verdict
    open_count = sum(
        1 for record in match.records if record.status is FindingStatus.OPEN
    )
    current = _run_record(
        request=request,
        round_number=round_number,
        verdict=verdict,
        resolved=len(match.resolved),
        open_after=open_count,
        convergence_score=score_records(records=match.records),
    )
    combined_runs = [*state.runs, current]
    return RoundOutcome(
        prior=state,
        round_number=round_number,
        match=match,
        verdict=verdict,
        open_count=open_count,
        run=current,
        runs=tuple(combined_runs[-MAX_STORED_RUNS:]),
        truncated=state.truncated or len(combined_runs) > MAX_STORED_RUNS,
    )


def advance_review_state(*, request: StickyRequest) -> ReviewState:
    """Advance artifact state by one completed review round.

    Matching, verdict derivation, and run-record construction are the same
    work :func:`build_sticky_bodies` uses to render the comment, so a persist
    path and a follow-up sticky cannot disagree about what this round did.

    Args:
        request: Inputs for this round.

    Returns:
        The state to persist: prior runs plus this round, matched findings,
        and this result's coverage records.
    """
    outcome = _round_outcome(request=request)
    state = outcome.prior
    result = request.result
    return ReviewState(
        version=STATE_VERSION,
        runs=outcome.runs,
        findings=outcome.match.records,
        coverage=result.coverage_records,
        flagged_files=result.flagged_files,
        pending_invalidations=result.pending_invalidations,
        consumed_flags=result.consumed_flags,
        repo=state.repo,
        pr_number=state.pr_number,
        base_sha=state.base_sha,
        head_sha=request.head_sha or state.head_sha,
        workflow=state.workflow,
        event=state.event,
        run_id=state.run_id,
        lintro_version=state.lintro_version,
        truncated=outcome.truncated,
    )


def _round_plan(*, request: StickyRequest, outcome: RoundOutcome) -> StickyPlan:
    """Resolve the inputs every section of this round's board reads.

    Args:
        request: Inputs for this round.
        outcome: What the round decided.

    Returns:
        StickyPlan: The plan the section renderers work from.
    """
    return StickyPlan(
        match=outcome.match,
        verdict=outcome.verdict,
        round_number=outcome.round_number,
        result=request.result,
        head_sha=request.head_sha,
        runs=outcome.runs,
        transport=request.transport,
        auth_mode=request.auth_mode,
        checklist_display=request.checklist_display,
        question_map=request.question_map or {},
        inline_failure=request.inline_failure,
        repo=request.repo,
        pr_number=request.pr_number,
    )


def build_sticky_bodies(*, request: StickyRequest) -> tuple[str, str | None]:
    """Compose the primary sticky and an optional history-archive comment.

    Args:
        request: Inputs for this round.

    Returns:
        ``(primary, archive)``. ``archive`` is ``None`` until history would
        push the primary past :data:`PRIMARY_SOFT_LIMIT`.
    """
    outcome = _round_outcome(request=request)
    plan = _round_plan(request=request, outcome=outcome)

    def render(*, limits: RenderLimits, archive_history: bool = False) -> str:
        """Render the primary body at the given limits.

        Args:
            limits: Per-section render limits to apply.
            archive_history: When True, history expanders become a link.

        Returns:
            str: The assembled primary body, without a state block.
        """
        return assemble(
            sections=round_sections(
                plan=plan,
                limits=limits,
                archive_history=archive_history,
            ),
            budget=None,
        )

    primary = fit_body(
        assemble=render,
        counts=SectionCounts(
            history_rows=max(len(outcome.runs) - 1, 0),
            open=outcome.open_count,
            resolved=len(outcome.match.resolved),
        ),
    )
    archive: str | None = None
    if len(primary) > PRIMARY_SOFT_LIMIT and len(outcome.runs) > 1:
        primary = render(limits=RenderLimits(), archive_history=True)
        archive = _archive_body(runs=outcome.runs, records=outcome.match.records)
        if len(primary) > MAX_COMMENT_CHARS:
            primary = cap_body(body=primary)
    return primary, archive


def render_state_sticky(
    *,
    state: ReviewState,
    banner: str = "",
    repo: str = "",
    pr_number: int | None = None,
) -> str:
    """Re-render the mission-control layout from persisted state alone.

    The sticky is rebuilt on every run from the state blob, so a round that
    never produced a :class:`ReviewResult` — a provider outage, an aborted CLI
    invocation — can still show the last good board instead of blanking it
    (#1954). Only the sections that describe *this* round are omitted: the
    summary, the model's reasoning, the fix-all prompt panel, and the ``This
    run`` badges all belong to a run that did not happen. Everything a reviewer
    navigates by — verdict, tiles, open findings, resolved findings, history —
    is derived from state and rendered unchanged.

    Args:
        state: Artifact or migrated sticky state. An empty state renders a
            defined first-failure surface rather than crashing.
        banner: Optional blockquote rendered directly under the header.
        repo: ``owner/name`` slug used to link finding titles.
        pr_number: Pull request number used for the same links.

    Returns:
        Primary sticky body with no hidden state blob.
    """
    records = state.findings
    runs = state.runs
    latest = runs[-1] if runs else None
    if latest is None and not records:
        return assemble(
            sections=[
                Section(name="marker", text=STICKY_MARKER),
                Section(name="header", text="## 🔎 Lintro Review — no prior review"),
                Section(
                    name="banner",
                    text=banner or "> No stored review state is available yet.",
                ),
                Section(name="footer", text=STICKY_FOOTER),
            ],
            budget=None,
        )
    open_count = sum(1 for record in records if record.status is FindingStatus.OPEN)
    plan = StickyPlan(
        match=FindingMatchResult(records=records),
        verdict=(
            latest.verdict if latest is not None else derive_verdict(findings=records)
        ),
        round_number=latest.round if latest is not None else 1,
        head_sha=latest.sha if latest is not None else "",
        runs=runs,
        repo=repo,
        pr_number=pr_number,
    )

    def render(*, limits: RenderLimits) -> str:
        """Render the whole state-only body at the given per-section limits.

        Args:
            limits: Per-section render limits to apply.

        Returns:
            str: The assembled body, without a state block.
        """
        return assemble(
            sections=state_sections(plan=plan, banner=banner, limits=limits),
            budget=None,
        )

    return fit_body(
        assemble=render,
        counts=SectionCounts(
            history_rows=max(len(runs) - 1, 0),
            open=open_count,
            resolved=len(records) - open_count,
        ),
    )
