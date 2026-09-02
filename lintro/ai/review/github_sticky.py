"""Sticky-comment assembly, state, and size capping for GitHub reviews.

The sticky comment is the PR's *mission control* (#1909, epic #1905): a living
status board edited in place on every run. It leads with the derived readiness
verdict and the round-over-round delta, then indexes the open findings — it
deliberately does **not** repeat the finding detail that already lives on the
inline comments.

Layout, top to bottom:

1. header — ``🔎 Lintro Review · round N · commit <sha>``
2. readiness pill, the verdict rubric as fine-print directly under it, then the
   delta line
3. ``Summary`` — headline plus walkthrough bullets, severity-marked when a
   bullet is tied to an open P1/P2
4. ``Why it's blocked`` — the model's reasoning and the files needing
   attention
5. severity tiles (blockers / warnings / nits / fixed)
6. ``Open findings`` — one line per finding, titles only
7. the fix-all agent prompt panel, scoped to *all* still-open findings
8. ``Resolved`` — struck-through titles with their fixing commit
9. *This run* badges, two single-row tables (model-first ordering)
10. ``---`` then exactly one ``🕘 Run history`` collapsible
11. a one-line footer

Two invariants the renderer enforces:

* **No nested ``<details>``.** Every collapsible is top level; the run history
  carries plain tables and the degraded fold-in flattens finding detail.
* **The comment (body + state block) always fits ``MAX_COMMENT_CHARS``.**
  Oldest run history is pruned first, then resolved findings, then open
  findings — each with a visible marker, never a silent drop. The state block
  is reserved when needed so appending it cannot push the total over the cap.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from loguru import logger

from lintro.ai.review.agent_prompts import render_agent_prompt_panel
from lintro.ai.review.checklist_display import (
    format_review_questions_markdown,
    questions_for_finding,
)
from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import (
    derive_verdict,
    match_findings,
    normalize_file_path,
)
from lintro.ai.review.github_constants import (
    _SEVERITY_EMOJI,
    ARCHIVE_MARKER,
    MAX_COMMENT_CHARS,
    MAX_STORED_RUNS,
    PRIMARY_SOFT_LIMIT,
    SHORT_SHA_LENGTH,
    STICKY_FOOTER,
    STICKY_MARKER,
)
from lintro.ai.review.github_lifecycle import inline_comment_url
from lintro.ai.review.github_render import (
    _fmt_cost,
    _fmt_int,
    _format_checklist_appendix_markdown,
    _severity_counts,
    format_coverage_limited_warning,
    format_timings_note,
    sanitize_comment_text,
)
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.patch_validation import describe_suggestion_drops
from lintro.ai.review.review_state_codec import (
    decode_state,
    prune_state_to_fit,
    render_state_block,
    renumber_if_legacy_v1,
)
from lintro.ai.review.severity_gate import count_downgrades
from lintro.ai.review.verdict import (
    VERDICT_RUBRIC_FINE_PRINT,
    apply_coverage_gate,
    resolve_bullet_finding,
    verdict_label,
)
from lintro.ai.transport import resolve_cost_basis

__all__ = [
    "advance_review_state",
    "build_sticky_bodies",
    "build_sticky_comment",
    "matcher_reviewed_paths",
    "parse_review_state",
    "parse_review_state_v2",
    "render_state_sticky",
    "stamp_comment_ids",
]


def matcher_reviewed_paths(*, result: ReviewResult) -> frozenset[str] | None:
    """Return the reviewed-path set the matcher should use.

    An empty ``metadata.reviewed_paths`` on a resume-aware result
    (``coverage`` is set) is a true empty set — including a zero-call
    carried round — so unread findings stay open. Fixture results and
    reviews that predate the coverage field still treat the empty tuple
    as unspecified (``None``) so disappeared findings can resolve.

    Args:
        result: Current review result.

    Returns:
        Paths the provider read, or ``None`` when the field is unspecified.
    """
    if result.metadata.reviewed_paths:
        return frozenset(result.metadata.reviewed_paths)
    if result.coverage is not None:
        return frozenset()
    return None


def stamp_comment_ids(
    *,
    records: tuple[FindingRecord, ...],
    comment_ids: Mapping[str, int] | None,
) -> tuple[FindingRecord, ...]:
    """Attach captured inline comment ids to the records about to be persisted.

    Args:
        records: Records produced by this round's matching.
        comment_ids: Finding key to inline comment id, or ``None`` when no ids
            were captured.

    Returns:
        The records, each carrying its comment id when one is known. A record
        keeps the id it already had when the capture found none, so a failed
        listing never erases the anchor a later round edits.
    """
    if not comment_ids:
        return records
    return tuple(
        (
            replace(record, inline_comment_id=comment_ids[record.key])
            if record.key in comment_ids
            else record
        )
        for record in records
    )


#: Emoji rendered next to each readiness verdict's label.
VERDICT_EMOJI: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "⛔",
    ReviewVerdict.CHANGES_REQUESTED: "⚠️",
    ReviewVerdict.NITS_ONLY: "🟡",
    ReviewVerdict.READY: "✅",
    ReviewVerdict.INCOMPLETE: "⚠️",
}

#: Heading used for the reasoning section, per verdict.
_REASONING_HEADINGS: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "Why it's blocked",
    ReviewVerdict.CHANGES_REQUESTED: "Why changes are requested",
    ReviewVerdict.NITS_ONLY: "Why it's flagged",
    ReviewVerdict.READY: "Why it's ready",
    ReviewVerdict.INCOMPLETE: "Why the verdict is withheld",
}

#: Noun naming the finding class that decides each verdict, for the pill.
_VERDICT_NOUNS: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "blocker",
    ReviewVerdict.CHANGES_REQUESTED: "warning",
    ReviewVerdict.NITS_ONLY: "nit",
    ReviewVerdict.READY: "finding",
    ReviewVerdict.INCOMPLETE: "file",
}

#: Severity that decides each verdict, used to count the pill's subject.
#: ``READY`` is deliberately absent — it is decided by the *absence* of open
#: findings, and :func:`_readiness_pill` returns before consulting this table.
_VERDICT_SEVERITY: dict[ReviewVerdict, Severity] = {
    ReviewVerdict.BLOCKED: Severity.P1,
    ReviewVerdict.CHANGES_REQUESTED: Severity.P2,
    ReviewVerdict.NITS_ONLY: Severity.P3,
}

# Import-time exhaustiveness guards, twins of the one in
# :mod:`lintro.ai.review.verdict`: a verdict added without a rendering entry
# must fail loudly at import rather than as a KeyError mid-render on a PR.
for _table_name, _table in (
    ("VERDICT_EMOJI", VERDICT_EMOJI),
    ("_REASONING_HEADINGS", _REASONING_HEADINGS),
    ("_VERDICT_NOUNS", _VERDICT_NOUNS),
):
    _missing = set(ReviewVerdict) - set(_table)
    if _missing:  # pragma: no cover - guards a future verdict
        raise RuntimeError(f"{_table_name} missing entries for: {_missing}")

# _VERDICT_SEVERITY is guarded separately because READY is deliberately absent
# from it. Without this a new non-READY verdict would pass the loop above and
# then KeyError inside _readiness_pill on a live PR.
_missing = (
    set(ReviewVerdict)
    - {ReviewVerdict.READY, ReviewVerdict.INCOMPLETE}
    - set(_VERDICT_SEVERITY)
)
if _missing:  # pragma: no cover - guards a future verdict
    raise RuntimeError(f"_VERDICT_SEVERITY missing entries for: {_missing}")

#: Emoji marking a tracked entry that is a question rather than a finding.
_QUESTION_EMOJI = "❓"

#: Matches a ``<details>``/``</details>`` tag in untrusted model text. The folded
#: finding detail sits *inside* a collapsible, so a model-written closing tag
#: would end it early and break the sticky's one-level-only structure.
_DETAILS_TAG_RE = re.compile(r"<(/?)(details|summary)\b", re.IGNORECASE)

#: Maximum characters of a finding title rendered in a table cell.
_TITLE_LIMIT = 160

#: End of the first sentence of a round narrative. Terminators other than the
#: period are matched too: a headline ending in "?" or "!" is one sentence, and
#: splitting on ". " alone would persist the whole paragraph after it.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

#: Maximum characters of a stored per-round narrative, on the way in (it is
#: persisted in the state blob, which competes for the same size cap) and on
#: the way out.
_NARRATIVE_LIMIT = 200

#: Upper bound for the finding-count binary searches. No review round
#: realistically reports more findings than this, and the search costs only
#: ~log2(n) renders.
_PRUNE_SEARCH_CEILING = 4096


class _Assembler(Protocol):
    """Callable that renders the whole sticky body at given section limits."""

    def __call__(self, *, limits: _RenderLimits) -> str:
        """Render the body.

        Args:
            limits: Per-section render limits to apply.

        Returns:
            The assembled body, without the hidden state block.
        """
        ...  # pragma: no cover - structural type only


@dataclass(frozen=True, slots=True)
class _RenderLimits:
    """How much of each prunable section the assembler may render.

    ``None`` means unlimited. Sections are pruned in the order the fields are
    declared, so the cheapest history is dropped before any finding is.

    Attributes:
        history: Number of *prior* runs shown in the run-history table, newest
            first. ``None`` shows every stored run.
        resolved: Number of resolved findings shown, newest first.
        open: Number of open findings shown (and covered by the fix-all
            prompt), highest severity first.
    """

    history: int | None = None
    resolved: int | None = None
    open: int | None = None


def _fit_body_with_state(
    *,
    assemble: _Assembler,
    prior_run_count: int,
    open_count: int,
    resolved_count: int,
    state: ReviewState,
) -> str:
    """Fit the visible body, then append state under ``MAX_COMMENT_CHARS``.

    Fits the visible body first, prunes and renders the state block against
    that body, and, when needed, refits the body with
    ``reserved=len(state_block)`` so the
    final concatenation is always ``<= MAX_COMMENT_CHARS`` (#1866).

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        prior_run_count: Number of prior runs available to the history table.
        open_count: Number of open findings, bounding that section's search.
        resolved_count: Number of resolved findings, likewise.
        state: State to embed in the hidden trailing block.

    Returns:
        Complete sticky body including the state block.
    """
    body = _fit_body(
        assemble=assemble,
        prior_run_count=prior_run_count,
        open_count=open_count,
        resolved_count=resolved_count,
    )
    pruned = prune_state_to_fit(state=state, body=body, limit=MAX_COMMENT_CHARS)
    state_block = render_state_block(state=pruned)
    if len(body) + len(state_block) > MAX_COMMENT_CHARS:
        # Body left no room for even the pruned-down state; refit with an
        # explicit reservation so appending the block cannot overflow.
        body = _fit_body(
            assemble=assemble,
            prior_run_count=prior_run_count,
            open_count=open_count,
            resolved_count=resolved_count,
            reserved=len(state_block),
        )
        pruned = prune_state_to_fit(state=state, body=body, limit=MAX_COMMENT_CHARS)
        state_block = render_state_block(state=pruned)
    if len(body) + len(state_block) > MAX_COMMENT_CHARS:
        # Last resort: pruning floors at one run with unshortened fields, so a
        # pathological record (e.g. a monster model-emitted title) can still
        # overflow. Post an *empty but authentic* state block rather than none:
        # the marker walk accepts the last well-formed block, so omitting ours
        # would let a forged marker in visible finding prose win. Cross-run
        # tracking still resets next round — strictly better than GitHub
        # rejecting the comment or trusting an attacker's state.
        logger.warning(
            "Sticky state block cannot fit the comment budget even after "
            "pruning; posting an empty state block (cross-run tracking "
            "resets).",
        )
        empty_block = render_state_block(
            state=ReviewState(runs=(), findings=(), truncated=True),
        )
        return _cap_body(body=body, reserved=len(empty_block)) + empty_block
    return body + state_block


def build_sticky_comment(
    *,
    result: ReviewResult,
    prior_runs: list[dict[str, Any]] | None = None,
    prior_state: ReviewState | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    diff_lines: dict[str, set[int]] | None = None,
    head_sha: str = "",
    transport: str = "",
    auth_mode: str = "",
    cost_basis: str = "",
    inline_failure: InlinePostFailure | None = None,
    inline_comment_ids: Mapping[str, int] | None = None,
    repo: str = "",
    pr_number: int | None = None,
    departed_paths: frozenset[str] | None = None,
) -> str:
    """Compose the primary sticky body. See :func:`build_sticky_bodies`."""
    primary, _archive = build_sticky_bodies(
        result=result,
        prior_runs=prior_runs,
        prior_state=prior_state,
        checklist_display=checklist_display,
        question_map=question_map,
        diff_lines=diff_lines,
        head_sha=head_sha,
        transport=transport,
        auth_mode=auth_mode,
        cost_basis=cost_basis,
        inline_failure=inline_failure,
        inline_comment_ids=inline_comment_ids,
        repo=repo,
        pr_number=pr_number,
        departed_paths=departed_paths,
    )
    return primary


def advance_review_state(
    *,
    result: ReviewResult,
    prior_state: ReviewState | None = None,
    prior_runs: list[dict[str, Any]] | None = None,
    head_sha: str = "",
    transport: str = "",
    auth_mode: str = "",
    cost_basis: str = "",
    inline_comment_ids: Mapping[str, int] | None = None,
    departed_paths: frozenset[str] | None = None,
) -> ReviewState:
    """Advance artifact state by one completed review round.

    Matching, verdict derivation, and run-record construction are the same
    work :func:`build_sticky_bodies` uses to render the comment, so a persist
    path and a follow-up sticky cannot disagree about what this round did.

    Args:
        result: Current review result.
        prior_state: Artifact or migrated sticky state.
        prior_runs: Legacy run mappings. Ignored when ``prior_state`` is given.
        head_sha: Head commit sha reviewed in this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        cost_basis: Provenance of the reported cost.
        inline_comment_ids: Finding key to inline comment id.
        departed_paths: Paths that left the diff this round.

    Returns:
        The state to persist: prior runs plus this round, matched findings,
        and this result's coverage records.
    """
    state = prior_state if prior_state is not None else _state_from_runs(prior_runs)
    round_number = state.next_round
    match = match_findings(
        previous=state,
        findings=result.findings,
        round_number=round_number,
        head_sha=head_sha,
        reviewed_paths=matcher_reviewed_paths(result=result),
        departed_paths=departed_paths,
    )
    match = replace(
        match,
        records=stamp_comment_ids(
            records=match.records,
            comment_ids=inline_comment_ids,
        ),
    )
    findings_verdict = derive_verdict(findings=match.records)
    if result.coverage is not None:
        verdict = apply_coverage_gate(
            findings_verdict=findings_verdict,
            coverage_complete=result.coverage.complete,
        )
    else:
        verdict = findings_verdict
    open_count = sum(
        1 for record in match.records if record.status is FindingStatus.OPEN
    )
    current = _run_record(
        result=result,
        round_number=round_number,
        head_sha=head_sha,
        transport=transport,
        auth_mode=auth_mode,
        cost_basis=cost_basis,
        verdict=verdict,
        resolved=len(match.resolved),
        open_after=open_count,
    )
    combined_runs = [*state.runs, current]
    truncated = state.truncated or len(combined_runs) > MAX_STORED_RUNS
    return ReviewState(
        version=3,
        runs=tuple(combined_runs[-MAX_STORED_RUNS:]),
        findings=match.records,
        coverage=result.coverage_records,
        flagged_files=result.flagged_files,
        pending_invalidations=result.pending_invalidations,
        consumed_flags=result.consumed_flags,
        repo=state.repo,
        pr_number=state.pr_number,
        base_sha=state.base_sha,
        head_sha=head_sha or state.head_sha,
        workflow=state.workflow,
        event=state.event,
        run_id=state.run_id,
        lintro_version=state.lintro_version,
        legacy=state.legacy,
        truncated=truncated,
    )


def build_sticky_bodies(
    *,
    result: ReviewResult,
    prior_runs: list[dict[str, Any]] | None = None,
    prior_state: ReviewState | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    diff_lines: dict[str, set[int]] | None = None,
    head_sha: str = "",
    transport: str = "",
    auth_mode: str = "",
    cost_basis: str = "",
    inline_failure: InlinePostFailure | None = None,
    inline_comment_ids: Mapping[str, int] | None = None,
    repo: str = "",
    pr_number: int | None = None,
    departed_paths: frozenset[str] | None = None,
) -> tuple[str, str | None]:
    """Compose the primary sticky and an optional history-archive comment.

    Args:
        result: Current review result.
        prior_runs: Legacy run records. Ignored when ``prior_state`` is given.
        prior_state: Artifact or migrated sticky state.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text.
        diff_lines: Unused; retained for the inline-posting interface.
        head_sha: Head commit sha reviewed in this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        cost_basis: Provenance of the reported cost.
        inline_failure: Findings whose inline comments could not be posted.
        inline_comment_ids: Finding key to inline comment id.
        repo: ``owner/name`` slug used to link finding titles.
        pr_number: Pull request number used for the same links.
        departed_paths: Paths that left the diff this round.

    Returns:
        ``(primary, archive)``. ``archive`` is ``None`` until history would
        push the primary past :data:`PRIMARY_SOFT_LIMIT`.
    """
    del diff_lines
    state = prior_state if prior_state is not None else _state_from_runs(prior_runs)
    round_number = state.next_round
    match = match_findings(
        previous=state,
        findings=result.findings,
        round_number=round_number,
        head_sha=head_sha,
        reviewed_paths=matcher_reviewed_paths(result=result),
        departed_paths=departed_paths,
    )
    match = replace(
        match,
        records=stamp_comment_ids(
            records=match.records,
            comment_ids=inline_comment_ids,
        ),
    )
    findings_verdict = derive_verdict(findings=match.records)
    if result.coverage is not None:
        verdict = apply_coverage_gate(
            findings_verdict=findings_verdict,
            coverage_complete=result.coverage.complete,
        )
    else:
        verdict = findings_verdict
    prior = list(state.runs)
    open_count = sum(
        1 for record in match.records if record.status is FindingStatus.OPEN
    )
    current = _run_record(
        result=result,
        round_number=round_number,
        head_sha=head_sha,
        transport=transport,
        auth_mode=auth_mode,
        cost_basis=cost_basis,
        verdict=verdict,
        resolved=len(match.resolved),
        open_after=open_count,
    )
    combined_runs = [*prior, current]
    all_runs = combined_runs[-MAX_STORED_RUNS:]

    def assemble(*, limits: _RenderLimits, archive_history: bool = False) -> str:
        """Render the primary body at the given limits."""
        return _assemble_body(
            result=result,
            match=match,
            verdict=verdict,
            round_number=round_number,
            head_sha=head_sha,
            runs=all_runs,
            transport=transport,
            auth_mode=auth_mode,
            checklist_display=checklist_display,
            question_map=question_map or {},
            inline_failure=inline_failure,
            limits=limits,
            repo=repo,
            pr_number=pr_number,
            archive_history=archive_history,
        )

    primary = _fit_body(
        assemble=assemble,
        prior_run_count=max(len(all_runs) - 1, 0),
        open_count=open_count,
        resolved_count=len(match.resolved),
    )
    archive: str | None = None
    if len(primary) > PRIMARY_SOFT_LIMIT and len(all_runs) > 1:
        primary = assemble(limits=_RenderLimits(), archive_history=True)
        archive = _archive_body(runs=all_runs, records=match.records)
        if len(primary) > MAX_COMMENT_CHARS:
            primary = _cap_body(body=primary, reserved=0)
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
    runs = list(state.runs)
    latest = runs[-1] if runs else None
    if latest is None and not records:
        return "\n\n".join(
            section
            for section in (
                STICKY_MARKER,
                "## 🔎 Lintro Review — no prior review",
                banner or "> No stored review state is available yet.",
                STICKY_FOOTER,
            )
            if section
        )
    open_count = sum(1 for record in records if record.status is FindingStatus.OPEN)

    def assemble(*, limits: _RenderLimits) -> str:
        """Render the whole state-only body at the given per-section limits."""
        return _assemble_state_body(
            state=state,
            banner=banner,
            round_number=latest.round if latest is not None else 1,
            head_sha=latest.sha if latest is not None else "",
            runs=runs,
            limits=limits,
            repo=repo,
            pr_number=pr_number,
        )

    return _fit_body(
        assemble=assemble,
        prior_run_count=max(len(runs) - 1, 0),
        open_count=open_count,
        resolved_count=len(records) - open_count,
    )


def _assemble_state_body(
    *,
    state: ReviewState,
    banner: str,
    round_number: int,
    head_sha: str,
    runs: list[RunRecord],
    limits: _RenderLimits,
    repo: str,
    pr_number: int | None,
) -> str:
    """Render the state-derived sticky sections and join the non-empty ones.

    Args:
        state: Persisted state to render.
        banner: Optional blockquote rendered directly under the header.
        round_number: Round number of the most recent successful run.
        head_sha: Head commit sha reviewed by that run.
        runs: Every retained run record, oldest first.
        limits: Per-section render limits.
        repo: ``owner/name`` slug used to link finding titles to their threads.
        pr_number: Pull request number used for the same links.

    Returns:
        The assembled body, without the hidden state block.
    """
    records = state.findings
    latest = runs[-1] if runs else None
    verdict = latest.verdict if latest is not None else derive_verdict(findings=records)
    match = FindingMatchResult(records=records)
    total_resolved = sum(
        1 for record in records if record.status is FindingStatus.RESOLVED
    )

    sections: list[str] = [
        STICKY_MARKER,
        _header(
            round_number=round_number,
            head_sha=head_sha,
            verdict=verdict,
        ),
        banner,
        _findings_round_section(
            match=match,
            result=None,
            round_number=round_number,
            head_sha=head_sha,
            verdict=verdict,
            limits=limits,
            repo=repo,
            pr_number=pr_number,
        ),
    ]
    history = _history_section(
        runs=runs,
        limit=limits.history,
        resolved_total=total_resolved,
        records=records,
    )
    if history:
        sections.extend(["---", history])
    sections.append(STICKY_FOOTER)
    return "\n\n".join(section for section in sections if section)


def _fit_body(
    *,
    assemble: _Assembler,
    prior_run_count: int,
    open_count: int,
    resolved_count: int,
    reserved: int = 0,
) -> str:
    """Shrink the rendered body until it fits the budget left for it.

    The budget is ``MAX_COMMENT_CHARS - reserved`` so the caller can hold space
    for the trailing state block (#1866). Pruning order is deliberate: history
    is the least valuable content on the comment, resolved findings are already
    fixed, and open findings are what a reader is actually here for, so they
    are trimmed last. Each stage leaves a visible marker, so nothing is ever
    dropped silently.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        prior_run_count: Number of prior runs available to the history table.
        open_count: Number of open findings, bounding that section's search.
        resolved_count: Number of resolved findings, likewise.
        reserved: Characters already claimed by the state block (or any other
            trailer) that must remain outside this body.

    Returns:
        A body at or under the remaining budget when that is reachable by
        pruning, else the smallest body pruning can produce, hard-truncated as
        a last resort.
    """
    limit = _body_char_limit(reserved=reserved)
    limits = _RenderLimits()
    body = assemble(limits=limits)
    if len(body) <= limit:
        return body

    # 1. Drop the oldest run history first, one round at a time.
    for history in range(prior_run_count - 1, -1, -1):
        limits = replace(limits, history=history)
        body = assemble(limits=limits)
        if len(body) <= limit:
            return body

    # 2. Then the oldest resolved findings — they are already fixed.
    fitted = _largest_fitting(
        assemble=assemble,
        limits=limits,
        field="resolved",
        ceiling=resolved_count,
        reserved=reserved,
    )
    if fitted is not None:
        return fitted

    # 3. Finally the open findings, keeping as many as fit. A verdict with no
    # substance is worse than an over-long comment the final cap will trim, so
    # the search floor is one finding, and one is still rendered when even that
    # overflows.
    limits = replace(limits, resolved=0)
    fitted = _largest_fitting(
        assemble=assemble,
        limits=limits,
        field="open",
        ceiling=open_count,
        minimum=1,
        reserved=reserved,
    )
    if fitted is None:
        fitted = assemble(limits=replace(limits, open=1))
    return _cap_body(body=fitted, reserved=reserved)


def _largest_fitting(
    *,
    assemble: _Assembler,
    limits: _RenderLimits,
    field: str,
    ceiling: int,
    minimum: int = 0,
    reserved: int = 0,
) -> str | None:
    """Binary-search the largest value of one limit whose body still fits.

    Both prunable finding sections order newest-first, so capping their count
    drops the oldest entries — the same oldest-first policy the run history
    follows.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        limits: Limits already applied to the cheaper sections.
        field: Name of the :class:`_RenderLimits` field to search over.
        ceiling: Number of entries the section actually has. Bounding the
            search by this rather than a fixed constant keeps the render count
            at ~log2(n) instead of ~12 renders for a three-finding round.
        minimum: Smallest count the section may be rendered at. Sections whose
            absence would hollow out the comment pass ``1`` so the search can
            never settle on showing none of them.
        reserved: Characters already claimed by the trailing state block.

    Returns:
        The body rendered at the largest fitting count, or ``None`` when not
        even ``minimum`` entries of that section make the body fit.
    """
    limit = _body_char_limit(reserved=reserved)
    best: str | None = None
    lower, upper = minimum, min(ceiling, _PRUNE_SEARCH_CEILING)
    while lower <= upper:
        middle = (lower + upper) // 2
        candidate = assemble(limits=replace(limits, **{field: middle}))
        if len(candidate) <= limit:
            best = candidate
            lower = middle + 1
        else:
            upper = middle - 1
    return best


def _body_char_limit(*, reserved: int) -> int:
    """Return the visible-body budget after reserving the state block.

    Args:
        reserved: Characters claimed by the trailing state block (or any other
            trailer that will be concatenated after the body).

    Returns:
        Non-negative character budget for the visible body alone.
    """
    return max(MAX_COMMENT_CHARS - max(reserved, 0), 0)


def _assemble_body(
    *,
    result: ReviewResult,
    match: FindingMatchResult,
    verdict: ReviewVerdict,
    round_number: int,
    head_sha: str,
    runs: list[RunRecord],
    transport: str,
    auth_mode: str,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
    inline_failure: InlinePostFailure | None,
    limits: _RenderLimits,
    repo: str = "",
    pr_number: int | None = None,
    archive_history: bool = False,
) -> str:
    """Render every sticky section in mockup order and join the non-empty ones.

    Args:
        result: Current review result.
        match: Cross-round matching outcome for this round.
        verdict: Readiness verdict, including the coverage gate.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.
        runs: Every retained run record, oldest first, current run last.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text.
        inline_failure: Findings whose inline comments could not be posted.
        limits: Per-section render limits.
        repo: ``owner/name`` slug used to link finding titles.
        pr_number: Pull request number used for the same links.
        archive_history: When True, history expanders are replaced by a link.

    Returns:
        The assembled primary body. No hidden state blob is appended.
    """
    open_findings = _sorted_open_findings(
        findings=result.findings,
        limit=limits.open,
    )
    total_resolved = sum(
        1 for record in match.records if record.status is FindingStatus.RESOLVED
    )
    sections: list[str] = [
        STICKY_MARKER,
        _header(
            round_number=round_number,
            head_sha=head_sha,
            verdict=verdict,
        ),
        _incomplete_banner(result=result, verdict=verdict),
        _coverage_section(result=result, verdict=verdict),
        _summary_section(result=result),
        _reasoning_section(result=result, verdict=verdict),
        _degraded_row(failure=inline_failure),
        _suggestion_drops_row(result=result),
        _coverage_limited_row(result=result),
        _findings_round_section(
            match=match,
            result=result,
            round_number=round_number,
            head_sha=head_sha,
            verdict=verdict,
            limits=limits,
            repo=repo,
            pr_number=pr_number,
        ),
        _degraded_details(
            failure=inline_failure,
            checklist_display=checklist_display,
            question_map=question_map,
            limit=limits.open,
        ),
        render_agent_prompt_panel(
            findings=open_findings,
            scope=AgentPromptScope(
                kind=AgentPromptScopeKind.ALL_OPEN,
                round_number=round_number,
            ),
        ),
        _this_run_section(
            result=result,
            transport=transport,
            auth_mode=auth_mode,
        ),
    ]
    if checklist_display is ChecklistDisplay.ALL:
        sections.append("\n".join(_format_checklist_appendix_markdown(result=result)))
    history = _history_section(
        runs=runs,
        limit=limits.history,
        resolved_total=total_resolved,
        archive_only=archive_history,
        records=match.records,
    )
    if history:
        sections.extend(["---", history])
    sections.append(STICKY_FOOTER)
    return "\n\n".join(section for section in sections if section)


# --- section renderers -------------------------------------------------------


def _header(
    *,
    round_number: int,
    head_sha: str,
    verdict: ReviewVerdict = ReviewVerdict.READY,
) -> str:
    """Render the sticky comment's title line.

    Args:
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round, possibly empty.
        verdict: Derived readiness verdict, including INCOMPLETE.

    Returns:
        The Markdown heading line with the verdict in the title.
    """
    del round_number, head_sha
    emoji = VERDICT_EMOJI[verdict]
    label = verdict_label(verdict=verdict)
    return f"## 🔎 Lintro Review — {emoji} {label}"


def _incomplete_banner(*, result: ReviewResult, verdict: ReviewVerdict) -> str:
    """Render the Variant B withheld-verdict callout.

    Args:
        result: Current review result, possibly with coverage counts.
        verdict: Derived verdict.

    Returns:
        A warning callout, or empty when the round is complete.
    """
    if verdict is not ReviewVerdict.INCOMPLETE or result.coverage is None:
        return ""
    counts = result.coverage
    cap = result.metadata.max_cost_usd
    cap_text = f"`${cap:.2f}`" if cap is not None else "the cost cap"
    awaiting = counts.awaiting
    eligible = counts.eligible
    reason_map = dict(result.awaiting_reasons)
    listed = result.awaiting_paths[:12]
    extra = max(len(result.awaiting_paths) - len(listed), 0)
    items: list[str] = []
    for path in listed:
        reason = reason_map.get(path, "")
        label = f"`{sanitize_comment_text(path, limit=200)}`"
        if reason:
            label = f"{label} — {sanitize_comment_text(reason, limit=160)}"
        items.append(label)
    if extra:
        items.append(f"*({extra} more)*")
    files_block = ""
    if items:
        files_block = (
            "\n>\n> <details><summary>"
            f"{awaiting} files awaiting review</summary>\n>\n> "
            + " · ".join(items)
            + "\n>\n> </details>"
        )
    return (
        "> [!WARNING]\n"
        f"> **Verdict withheld — {awaiting} of {eligible} files not yet "
        f"reviewed.** {cap_text} stopped this round after {counts.reviewed} "
        "files. The check stays ❌ until every file is covered at HEAD; "
        "the next round resumes with the unreviewed files first."
        f"{files_block}\n"
    )


def _coverage_section(*, result: ReviewResult, verdict: ReviewVerdict) -> str:
    """Render the Variant B coverage table.

    Args:
        result: Current review result.
        verdict: Derived verdict.

    Returns:
        The coverage table, or empty when the round is complete.
    """
    if verdict is not ReviewVerdict.INCOMPLETE or result.coverage is None:
        return ""
    counts = result.coverage
    return "\n".join(
        [
            "### Coverage this round",
            "",
            "| reviewed now | carried forward | awaiting review "
            "| invalidated (re-queued) |",
            "|:-:|:-:|:-:|:-:|",
            (
                f"| **{counts.reviewed}** | {counts.carried} | "
                f"**{counts.awaiting}** | {counts.invalidated} |"
            ),
        ],
    )


def _findings_round_section(
    *,
    match: FindingMatchResult,
    result: ReviewResult | None,
    round_number: int,
    head_sha: str,
    verdict: ReviewVerdict,
    limits: _RenderLimits,
    repo: str,
    pr_number: int | None,
) -> str:
    """Render the Findings heading and the combined Δ table.

    Args:
        match: Cross-round matching outcome.
        result: Current result, or ``None`` on a state-only re-render.
        round_number: Current round.
        head_sha: Head sha for this round.
        verdict: Derived verdict, including the coverage gate.
        limits: Per-section render limits.
        repo: Repository slug for inline links.
        pr_number: Pull request number for inline links.

    Returns:
        The Findings section.
    """
    open_records = _sorted_open_records(records=match.records, limit=limits.open)
    fixed_now = [
        record
        for record in match.records
        if record.status is FindingStatus.RESOLVED
        and record.resolved_round == round_number
    ]
    if limits.resolved is not None:
        fixed_now = fixed_now[: limits.resolved]
    coverage_label = _findings_coverage_label(result=result, verdict=verdict)
    short = _short_sha(sha=head_sha)
    sha_bit = f" · `{short}`" if short else ""
    open_count = sum(
        1 for record in match.records if record.status is FindingStatus.OPEN
    )
    heading = (
        f"### Findings · Round {round_number}{sha_bit} · {coverage_label} · "
        f"{open_count} open · {len(fixed_now)} fixed this round"
    )
    if not open_records and not fixed_now:
        return f"{heading}\n\n✅ Nothing open."
    lines = [
        heading,
        "",
        "| Δ | Sev | Finding | Where | Since |",
        "|:-:|:-:|---|---|---|",
    ]
    for record in open_records:
        lines.append(
            f"| {_delta_cell(record=record, match=match)} "
            f"| {_severity_cell(record=record)} "
            f"| {_finding_cell(record=record, repo=repo, pr_number=pr_number)} "
            f"| `{_location(record=record)}` "
            f"| round {record.since_round} |",
        )
    for record in fixed_now:
        lines.append(
            f"| ✔ fixed "
            f"| {_severity_cell(record=record)} "
            f"| ~~{_cell(text=record.title, limit=_TITLE_LIMIT)}~~ "
            f"| `{_location(record=record)}` "
            f"| round {record.since_round} |",
        )
    dropped = open_count - len(open_records)
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} more open "
                f"{_plural(count=dropped, noun='finding')} not listed** to fit "
                "GitHub's size limit — see the inline comments and the workflow "
                "run log.",
            ],
        )
    return "\n".join(lines)


def _findings_coverage_label(
    *,
    result: ReviewResult | None,
    verdict: ReviewVerdict,
) -> str:
    """Return the coverage fragment for the Findings heading."""
    del verdict
    if result is None or result.coverage is None:
        return "coverage n/a"
    counts = result.coverage
    if counts.complete:
        return f"✅ {counts.covered_at_head}/{counts.eligible} at HEAD"
    return f"⚠️ {counts.covered_at_head}/{counts.eligible} files"


def _readiness_pill(
    *,
    verdict: ReviewVerdict,
    records: tuple[FindingRecord, ...],
) -> str:
    """Render the derived readiness verdict and what decided it.

    Args:
        verdict: Readiness verdict derived from the open findings.
        records: Every tracked finding record.

    Returns:
        A single bold line, for example ``**⛔ Blocked** — 1 open blocker``.
    """
    label = f"**{VERDICT_EMOJI[verdict]} {verdict_label(verdict=verdict)}**"
    if verdict is ReviewVerdict.READY:
        return f"{label} — no open findings"
    severity = _VERDICT_SEVERITY[verdict]
    count = sum(
        1
        for record in records
        if record.status is FindingStatus.OPEN
        and not record.is_question
        and record.severity is severity
    )
    noun = _plural(count=count, noun=_VERDICT_NOUNS[verdict])
    return f"{label} — {count} open {noun}"


def _verdict_explainer() -> str:
    """Render the verdict-derivation rubric as fine-print under the pill.

    It sits directly under the readiness verdict, not inside the reasoning
    section: the rubric explains the *pill*, and a reader who wants to know how
    "Blocked" was decided should not have to find it three sections later. It is
    rendered on every round, including a clean ``READY`` one — that is precisely
    the round where a reader most needs to know the verdict was derived from
    open findings rather than asked of the model.

    Returns:
        The ``<sub>``-wrapped rubric line.
    """
    return f"<sub>{VERDICT_RUBRIC_FINE_PRINT}</sub>"


def _delta_line(*, match: FindingMatchResult, round_number: int) -> str:
    """Render the round-over-round finding delta.

    Args:
        match: Cross-round matching outcome for this round.
        round_number: 1-based round number for this run.

    Returns:
        The delta line, or an empty string on round 1 where there is nothing
        to compare against.
    """
    if round_number <= 1:
        return ""
    parts = [
        f"✔ {len(match.resolved)} resolved",
        f"**{len(match.new)} new**",
    ]
    # A regressed finding was resolved and came back, so it is emphatically not
    # unchanged — and the open table already labels it "↩ regressed". Folding it
    # into the unchanged count made the two contradict each other.
    if match.regressed:
        parts.append(f"↩ {len(match.regressed)} regressed")
    parts.append(f"{len(match.carried)} unchanged since round {round_number - 1}")
    return " · ".join(parts)


def _summary_section(*, result: ReviewResult) -> str:
    """Render the one-line summary plus severity-marked walkthrough bullets.

    A bullet tied to an open P1/P2 finding carries that finding's severity dot
    and is bolded, so a blocker can never read as neutral prose in the
    walkthrough.

    Args:
        result: Current review result.

    Returns:
        The ``Summary`` section, or an empty string when nothing was returned.
    """
    summary = result.pr_summary
    headline = sanitize_comment_text(
        (summary.headline if summary else "") or result.summary or "",
        limit=1000,
    ).strip()
    bullets = summary.walkthrough if summary else ()
    if not headline and not bullets:
        return ""

    lines = ["### Summary"]
    if headline:
        lines.extend(["", headline])
    if bullets:
        lines.append("")
        lines.extend(
            _summary_bullet(
                text=bullet.text,
                finding_ref=bullet.finding_ref,
                result=result,
            )
            for bullet in bullets
        )
    return "\n".join(lines)


def _summary_bullet(*, text: str, finding_ref: str, result: ReviewResult) -> str:
    """Render one walkthrough bullet, severity-marked when it names a blocker.

    Args:
        text: Bullet text as written by the model.
        finding_ref: The bullet's ``file:line`` finding reference, possibly
            empty.
        result: Current review result, used to resolve the reference.

    Returns:
        The Markdown list item.
    """
    safe = sanitize_comment_text(text, limit=500).strip()
    finding = resolve_bullet_finding(
        finding_ref=finding_ref,
        findings=result.findings,
    )
    if finding is None or finding.severity not in {Severity.P1, Severity.P2}:
        return f"- {safe}"
    return f"- {_SEVERITY_EMOJI[finding.severity]} **{safe}**"


def _reasoning_section(*, result: ReviewResult, verdict: ReviewVerdict) -> str:
    """Render the model's verdict reasoning and the files it points at.

    The derivation rubric is deliberately *not* here: it explains the readiness
    pill and is rendered directly under it by :func:`_verdict_explainer`.

    Args:
        result: Current review result.
        verdict: Readiness verdict derived from the open findings.

    Returns:
        The reasoning section, or an empty string when there is nothing to
        explain (no reasoning and nothing open).
    """
    reasoning = result.verdict_reasoning
    has_reasoning = reasoning is not None and not reasoning.is_empty
    if not has_reasoning and verdict is ReviewVerdict.READY:
        return ""

    lines = [f"### {_REASONING_HEADINGS[verdict]}"]
    if reasoning is not None:
        for paragraph in (reasoning.deciding_factor, reasoning.failure_mechanism):
            text = sanitize_comment_text(paragraph, limit=2000).strip()
            if text:
                lines.extend(["", text])
    if reasoning is not None and reasoning.files_needing_attention:
        files = " · ".join(
            f"`{sanitize_comment_text(path, limit=200)}`"
            for path in reasoning.files_needing_attention
        )
        lines.extend(["", f"**Files needing attention:** {files}"])
    return "\n".join(lines)


def _tiles_section(*, records: tuple[FindingRecord, ...]) -> str:
    """Render the compact severity tiles, including the cumulative fixed count.

    Args:
        records: Every tracked finding record.

    Returns:
        A three-row Markdown table.
    """
    counts = dict.fromkeys((Severity.P1, Severity.P2, Severity.P3), 0)
    fixed = 0
    for record in records:
        # A question that stopped being asked was never a defect, so it must not
        # inflate the "fixed" tile, which reads as remediated findings.
        if record.is_question:
            continue
        if record.status is FindingStatus.RESOLVED:
            fixed += 1
            continue
        counts[record.severity] = counts.get(record.severity, 0) + 1
    return "\n".join(
        [
            "| 🔴 blockers | 🟠 warnings | 🟡 nits | ✔ fixed |",
            "|:-:|:-:|:-:|:-:|",
            (
                f"| **{counts[Severity.P1]}** | **{counts[Severity.P2]}** | "
                f"**{counts[Severity.P3]}** | **{fixed}** |"
            ),
        ],
    )


def _suggestion_drops_row(*, result: ReviewResult) -> str:
    """Render the warning row shown when patch validation dropped suggestions.

    A suggestion that no longer matches the file at head is withheld rather
    than posted (#2101). Withholding it silently would leave the reader
    believing the model simply had no mechanical fix, so the count and the
    reasons are stated on the sticky next to the other no-silent-caps notices.

    Args:
        result: Current review result.

    Returns:
        A blockquote warning naming the count and reasons, or an empty string
        when every suggestion validated.
    """
    notice = describe_suggestion_drops(findings=result.findings)
    if not notice:
        return ""
    return (
        f"> ✂️ **{sanitize_comment_text(notice, limit=300)}** — the described "
        "fix is kept on each finding; only the one-click commit is withheld."
    )


def _coverage_limited_row(*, result: ReviewResult) -> str:
    """Render the warning row shown when a findings cap limited this round.

    Sits with the other no-silent-caps notices (``_degraded_row``,
    ``_suggestion_drops_row``) and shares its text with the per-review body
    through :func:`format_coverage_limited_warning`, so the sticky can never
    present a capped round as an unmarked complete one (#2003).

    Args:
        result: Current review result.

    Returns:
        A blockquote warning, or an empty string when coverage was complete.
    """
    return format_coverage_limited_warning(metadata=result.metadata)


def _degraded_row(*, failure: InlinePostFailure | None) -> str:
    """Render the warning row shown when inline posting failed.

    Args:
        failure: Findings whose inline comments could not be posted.

    Returns:
        A blockquote warning naming the count and cause, or an empty string
        when inline posting succeeded.
    """
    if failure is None or failure.is_empty:
        return ""
    noun = _plural(count=failure.count, noun="finding")
    reason = sanitize_comment_text(failure.reason, limit=200).strip()
    cause = f" ({reason})" if reason else ""
    surface = "an inline comment" if failure.count == 1 else "inline comments"
    return (
        f"> ⚠️ **{failure.count} {noun} could not be posted as {surface}**"
        f"{cause}. Full details are folded in below instead."
    )


def _open_findings_section(
    *,
    records: list[FindingRecord],
    match: FindingMatchResult,
    total: int,
    repo: str = "",
    pr_number: int | None = None,
) -> str:
    """Render the open-findings index table.

    Titles only, one line each: the detail lives on the inline comments, and
    duplicating it here is what made the previous sticky unreadable. Each title
    links to that detail so the index is one click from the thread.

    Args:
        records: Open records to render, already ordered and limited.
        match: Cross-round matching outcome, for the ``Δ`` column.
        total: Total number of open findings before any limit was applied.
        repo: ``owner/name`` slug used to build the per-finding links.
        pr_number: Pull request number used for the same links.

    Returns:
        The ``Open findings`` section, always present so a reader never has to
        infer that nothing is open.
    """
    if total == 0:
        return "### Open findings (0)\n\n✅ Nothing open."

    lines = [
        f"### Open findings ({total})",
        "",
        "| Δ | Sev | Finding | Where | Since |",
        "|:-:|:-:|---|---|---|",
    ]
    for record in records:
        lines.append(
            f"| {_delta_cell(record=record, match=match)} "
            f"| {_severity_cell(record=record)} "
            f"| {_finding_cell(record=record, repo=repo, pr_number=pr_number)} "
            f"| `{_location(record=record)}` "
            f"| round {record.since_round} |",
        )
    dropped = total - len(records)
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} more open "
                f"{_plural(count=dropped, noun='finding')} not listed** to fit "
                "GitHub's size limit — see the inline comments and the workflow "
                "run log.",
            ],
        )
    return "\n".join(lines)


def _degraded_details(
    *,
    failure: InlinePostFailure | None,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
    limit: int | None = None,
) -> str:
    """Fold full finding detail into the sticky when inline posting failed.

    Rendered flat inside a single ``<details>``: the sticky's no-nesting rule
    means this cannot reuse the inline comment renderer, which carries its own
    collapsible.

    Args:
        failure: Findings whose inline comments could not be posted.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked questions.
        limit: Maximum number of findings to fold in. Shares the open-finding
            limit so this section shrinks under the same size pressure instead
            of being left to blunt tail truncation.

    Returns:
        A single-level collapsible carrying each failed finding's detail, or an
        empty string when inline posting succeeded.
    """
    if failure is None or failure.is_empty:
        return ""

    shown = failure.findings if limit is None else failure.findings[:limit]
    lines = [
        f"<details><summary>📋 Details for {failure.count} "
        f"{_plural(count=failure.count, noun='finding')} not posted inline"
        "</summary>",
        "",
    ]
    for finding in shown:
        lines.extend(
            _folded_finding(
                finding=finding,
                checklist_display=checklist_display,
                question_map=question_map,
            ),
        )
    dropped = failure.count - len(shown)
    if dropped > 0:
        lines.append(
            f"> ✂️ **{dropped} more failed "
            f"{_plural(count=dropped, noun='finding')} not detailed** to fit "
            "GitHub's size limit — see the workflow run log.",
        )
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def _folded_finding(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
) -> list[str]:
    """Render one finding's detail flat, with no collapsible of its own.

    Args:
        finding: Finding to render.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked questions.

    Returns:
        Markdown lines for the finding.
    """
    emoji = (
        _QUESTION_EMOJI if finding.is_question else _SEVERITY_EMOJI[finding.severity]
    )
    label = "question" if finding.is_question else finding.severity.value
    location = _inline_safe(text=finding.file, limit=200)
    where = f"`{location}:{finding.line}`" if finding.line > 0 else f"`{location}`"
    lines = [
        f"**{emoji} {label}** · `{_inline_safe(text=finding.category, limit=60)}`"
        f" — **{_inline_safe(text=finding.title, limit=_TITLE_LIMIT)}** · {where}",
        "",
        _inline_safe(text=finding.description, limit=2000),
    ]
    for heading, text in (("Cause", finding.cause), ("Fix", finding.fix)):
        body = _inline_safe(text=text, limit=2000).strip()
        if body:
            lines.extend(["", f"**{heading}:** {body}"])
    if checklist_display in {ChecklistDisplay.LINKED, ChecklistDisplay.ALL}:
        linked = format_review_questions_markdown(
            questions=questions_for_finding(
                finding=finding,
                question_map=question_map,
            ),
        )
        if linked.strip():
            lines.append(linked)
    lines.append("")
    return lines


def _resolved_section(*, records: list[FindingRecord], total: int) -> str:
    """Render the resolved-findings table with fixing-commit provenance.

    Args:
        records: Resolved records to render, already ordered and limited.
        total: Total number of resolved findings before any limit was applied.

    Returns:
        The ``Resolved`` section, or an empty string when nothing is resolved.
    """
    if total == 0:
        return ""

    lines = [
        f"### ✔ Resolved ({total})",
        "",
        "| Sev | Finding | Fixed in |",
        "|:-:|---|---|",
    ]
    for record in records:
        short = _short_sha(sha=record.resolved_sha)
        fixed_in = f"`{short}` · round {record.resolved_round}" if short else "—"
        lines.append(
            f"| {_severity_cell(record=record)} "
            f"| ~~{_cell(text=record.title, limit=_TITLE_LIMIT)}~~ "
            f"| {fixed_in} |",
        )
    dropped = total - len(records)
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} older resolved "
                f"{_plural(count=dropped, noun='finding')} not listed** "
                "(history truncated to fit GitHub's size limit).",
            ],
        )
    return "\n".join(lines)


def _this_run_section(
    *,
    result: ReviewResult,
    transport: str,
    auth_mode: str,
) -> str:
    """Render the two badge tables describing the current run.

    Both rows use the same badge-table renderer as the per-review body's run
    stats, and the primary row's cells come from the shared
    ``run_stats_primary_cells``, so the model, cost, and token figures cannot
    drift between the two surfaces (#1955). The secondary row is this
    surface's own: the status board omits the body's ``strictness`` and
    ``lintro`` version. Ordering is fixed across every surface (epic #1905):
    model, est. cost, tokens in, tokens out on row 1;
    transport and mechanics on row 2. No figure is presented as billed — the
    ``transport`` badge and the ``~`` prefix carry that honesty.

    Args:
        result: Current review result.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.

    Returns:
        The ``This run`` section.
    """
    metadata = result.metadata
    usage = metadata.token_usage
    prefix = "~" if metadata.token_usage_estimated else ""
    model = sanitize_comment_text(metadata.model or "unknown", limit=80)
    source = metadata.model_source
    model_cell = f"`{model}`" + (f" ({source})" if source else "")
    return "\n".join(
        [
            "**This run**",
            "",
            "| model | transport | est. cost | tokens in / out | depth "
            "| files | checks | duration |",
            "| --- | --- | --- | --- |:-:|:-:|:-:|---|",
            (
                f"| {model_cell} "
                f"| {_transport_label(transport=transport, auth_mode=auth_mode)} "
                f"| {_fmt_cost(metadata.cost_estimate_usd, estimated=True)} "
                f"| {prefix}{_fmt_int(int(usage.get('prompt', 0)))} / "
                f"{prefix}{_fmt_int(int(usage.get('completion', 0)))} "
                f"| {metadata.depth} "
                f"| {metadata.files_reviewed} "
                f"| {metadata.checklist_items} "
                f"| {metadata.duration_seconds:.0f}s |"
            ),
            *(
                ["", timings_note]
                if (timings_note := format_timings_note(metadata=metadata))
                else []
            ),
        ],
    )


def _history_section(
    *,
    runs: list[RunRecord],
    limit: int | None,
    resolved_total: int,
    archive_only: bool = False,
    records: tuple[FindingRecord, ...] = (),
) -> str:
    """Render the single run-history collapsible.

    Everything historical lives here and nowhere else: cumulative badges, the
    per-run table, and one mini-summary line per prior round. It is the only
    collapsible in the lower half of the comment, and it never nests another.

    Args:
        runs: Every retained run record, oldest first, current run last.
        limit: Number of *prior* runs to include, newest first. ``None``
            includes them all.
        resolved_total: Number of findings resolved across every round.
        archive_only: When True, emit the archive heading without expanders.
        records: Finding records used for per-round severity tiles.

    Returns:
        The collapsible, or an empty string on the first round where there is
        no history to show.
    """
    if len(runs) < 2:
        return ""

    total_cost = sum(run.cost for run in runs)
    total_tokens = sum(run.total for run in runs)
    estimated = any(run.estimated for run in runs)
    prefix = "~" if estimated else ""

    if limit is None:
        shown = runs
    else:
        # Clamp before slicing: a limit above the prior-run count would make the
        # start index negative and silently show the *newest* few instead of all.
        keep = min(max(limit, 0), len(runs) - 1)
        shown = [*runs[:-1][len(runs) - 1 - keep :], runs[-1]]
    dropped = len(runs) - len(shown)

    previous = max(len(runs) - 1, 0)
    heading = (
        f"### 🕘 History · {previous} previous "
        f"{_plural(count=previous, noun='run')} · "
        f"{resolved_total} fixed · "
        f"{_fmt_cost(total_cost, estimated=estimated)} · "
        f"{prefix}{_fmt_compact(value=total_tokens)} tokens"
    )
    if archive_only:
        return (
            f"{heading}\n\n"
            "Per-round expanders live on the archive comment "
            f"({ARCHIVE_MARKER.replace('<!-- ', '').replace(' -->', '')})."
        )
    tiles = _tiles_section(records=records) if records else ""
    expanders = [
        _round_expander(run=run, records=records) for run in reversed(shown[:-1])
    ]
    if dropped > 0:
        expanders.append(
            f"> ✂️ **{dropped} older "
            f"{_plural(count=dropped, noun='run')} not listed** "
            "(history truncated to fit GitHub's size limit).",
        )
    inner = "\n\n".join(part for part in [tiles, *expanders] if part)
    return (
        f"{heading}\n\n<details>"
        f"<summary>Run-by-run history</summary>\n\n{inner}\n\n</details>"
    )


def _history_row(*, run: RunRecord, latest: bool) -> str:
    """Render one row of the per-run history table.

    ``Open`` is what was still open *after* the round, not what the round
    raised: a round that reported three findings and fixed two of them left one
    open, and the raised count told that story backwards. A record persisted
    before those counts existed renders the raised total and ``—`` rather than
    a fabricated zero.

    Args:
        run: Run record to render.
        latest: True when this is the most recent run.

    Returns:
        A single Markdown table row.
    """
    prefix = "~" if run.estimated else ""
    short = _short_sha(sha=run.sha)
    open_after = (
        run.open_after if run.open_after is not None else run.p1 + run.p2 + run.p3
    )
    fixed = "—" if run.resolved is None else str(run.resolved)
    return (
        f"| {run.round}{' (latest)' if latest else ''} "
        f"| {f'`{short}`' if short else '—'} "
        f"| {VERDICT_EMOJI[run.verdict]} {verdict_label(verdict=run.verdict).lower()} "
        f"| `{_cell(text=run.model or 'unknown', limit=60)}` "
        f"| {open_after} "
        f"| {fixed} "
        f"| {prefix}{_fmt_int(run.prompt)} / {prefix}{_fmt_int(run.completion)} "
        f"| {_fmt_cost(run.cost, estimated=run.estimated)} "
        f"| {run.duration:.0f}s |"
    )


def _round_expander(
    *,
    run: RunRecord,
    records: tuple[FindingRecord, ...],
) -> str:
    """Render one prior-round expander (narrative, fixed table, this-run row).

    Args:
        run: Prior run record.
        records: All tracked findings, used for that round's fixes.

    Returns:
        A ``<details>`` block for the round.
    """
    short = _short_sha(sha=run.sha)
    sha_bit = f" · <code>{short}</code>" if short else ""
    open_after = (
        run.open_after if run.open_after is not None else run.p1 + run.p2 + run.p3
    )
    fixed = 0 if run.resolved is None else run.resolved
    prefix = "~" if run.estimated else ""
    summary = (
        f"<b>Round {run.round}</b>{sha_bit} · "
        f"{VERDICT_EMOJI[run.verdict]} {verdict_label(verdict=run.verdict).lower()} · "
        f"{fixed} fixed, {open_after} left open · "
        f"{_fmt_cost(run.cost, estimated=run.estimated)} · "
        f"{run.duration:.0f}s"
    )
    narrative = _DETAILS_TAG_RE.sub(
        r"&lt;\1\2",
        _cell(text=run.narrative, limit=_NARRATIVE_LIMIT),
    )
    lines = [
        f"<details><summary>{summary}</summary>",
        "",
    ]
    if narrative:
        lines.extend([f"> {narrative}", ""])
    fixed_rows = [
        record
        for record in records
        if record.status is FindingStatus.RESOLVED
        and record.resolved_round == run.round
        and not record.is_question
    ]
    if fixed_rows:
        lines.extend(
            [
                "**Fixed this round**",
                "",
                "| Sev | Finding |",
                "|:-:|---|",
            ],
        )
        for record in fixed_rows:
            lines.append(
                f"| {_severity_cell(record=record)} "
                f"| ~~{_cell(text=record.title, limit=_TITLE_LIMIT)}~~ |",
            )
        lines.append("")
    transport = _transport_label(transport=run.transport, auth_mode=run.auth_mode)
    lines.extend(
        [
            "| model | transport | est. cost | tokens in / out | depth "
            "| files | checks | duration |",
            "| --- | --- | --- | --- |:-:|:-:|:-:|---|",
            (
                f"| `{_cell(text=run.model or 'unknown', limit=60)}` "
                f"| {transport} "
                f"| {_fmt_cost(run.cost, estimated=run.estimated)} "
                f"| {prefix}{_fmt_int(run.prompt)} / "
                f"{prefix}{_fmt_int(run.completion)} "
                f"| {run.depth} "
                f"| {run.files_reviewed} "
                f"| {run.checks} "
                f"| {run.duration:.0f}s |"
            ),
            "",
            "</details>",
        ],
    )
    return "\n".join(lines)


def _archive_body(
    *,
    runs: list[RunRecord],
    records: tuple[FindingRecord, ...],
) -> str:
    """Render the archive sticky that holds per-round expanders.

    Args:
        runs: Every retained run, oldest first.
        records: Tracked findings.

    Returns:
        Archive comment body, truncated if it exceeds the comment budget.
    """
    expanders = [
        _round_expander(run=run, records=records) for run in reversed(runs[:-1])
    ]
    body = "\n\n".join(
        [
            ARCHIVE_MARKER,
            "## 🔎 Lintro Review — history archive",
            "Older rounds moved here so the primary sticky can keep this-round "
            "content. The primary comment still carries heading aggregates.",
            *expanders,
            STICKY_FOOTER,
        ],
    )
    if len(body) <= MAX_COMMENT_CHARS:
        return body
    # Oldest expanders degrade to their summary line.
    trimmed: list[str] = [
        ARCHIVE_MARKER,
        "## 🔎 Lintro Review — history archive",
        "Older rounds moved here so the primary sticky can keep this-round " "content.",
    ]
    for run in reversed(runs[:-1]):
        candidate = _round_expander(run=run, records=records)
        probe = "\n\n".join([*trimmed, candidate, STICKY_FOOTER])
        if len(probe) > MAX_COMMENT_CHARS:
            short = _short_sha(sha=run.sha)
            trimmed.append(
                f"**Round {run.round}**"
                + (f" · `{short}`" if short else "")
                + f" · {verdict_label(verdict=run.verdict).lower()}",
            )
            continue
        trimmed.append(candidate)
    trimmed.append(STICKY_FOOTER)
    return _cap_body(body="\n\n".join(trimmed), reserved=0)


def _history_mini_summary(*, run: RunRecord) -> str:
    """Render one prior round's recap under the history table.

    The round line names the verdict; the line under it is the model's own
    one-sentence account of that round when it wrote one, because "🔴 1 · 🟠 2"
    says how many things were wrong and never what they were. A record with no
    stored narrative — a legacy one, or a round whose model returned no summary
    — falls back to the severity counts.

    Args:
        run: Prior run record to summarize.

    Returns:
        Markdown for the recap, as a round line plus its detail line.
    """
    short = _short_sha(sha=run.sha)
    where = f" · `{short}`" if short else ""
    head = (
        f"**Round {run.round}**{where} · "
        f"{VERDICT_EMOJI[run.verdict]} {verdict_label(verdict=run.verdict).lower()}"
        + (" · ⚠️ partial" if run.partial else "")
        + (" · ⚠️ coverage limited" if run.coverage_limited else "")
    )
    # Table-safe *and* collapsible-safe: the recap sits inside the history
    # <details>, so a model-written closing tag would end it early.
    narrative = _DETAILS_TAG_RE.sub(
        r"&lt;\1\2",
        _cell(text=run.narrative, limit=_NARRATIVE_LIMIT),
    )
    detail = narrative or f"🔴 {run.p1} · 🟠 {run.p2} · 🟡 {run.p3}"
    return f"{head}\n{detail}"


# --- ordering and cell helpers ----------------------------------------------


def _record_sort_key(record: FindingRecord) -> tuple[str, str, int]:
    """Return the presentation sort key for a finding record."""
    return (record.severity.value, record.file, record.line)


def _sorted_open_records(
    *,
    records: tuple[FindingRecord, ...],
    limit: int | None,
) -> list[FindingRecord]:
    """Return open records in presentation order, optionally truncated.

    Args:
        records: Every tracked finding record.
        limit: Maximum number to return, or ``None`` for all.

    Returns:
        Open records sorted by severity, then file, then line.
    """
    ordered = sorted(
        (record for record in records if record.status is FindingStatus.OPEN),
        key=_record_sort_key,
    )
    return ordered if limit is None else ordered[:limit]


def _sorted_open_findings(
    *,
    findings: tuple[ReviewFinding, ...],
    limit: int | None,
) -> tuple[ReviewFinding, ...]:
    """Return this round's findings in the same order as the open table.

    Every open finding is, by construction, reported in the current round: the
    matcher resolves any prior record this round did not repeat. Sorting both
    the table and the prompt by the same key keeps them aligned without pairing
    records to findings one by one.

    Args:
        findings: This round's findings.
        limit: Maximum number to return, or ``None`` for all.

    Returns:
        Findings sorted by severity, then file, then line.
    """
    # Records store the *normalized* path, so sorting findings by the raw one
    # would let ``limit`` select a different subset for the prompt than for the
    # table (for example "./z.py" vs "a.py").
    ordered = sorted(
        findings,
        key=lambda finding: (
            finding.severity.value,
            normalize_file_path(finding.file),
            finding.line,
        ),
    )
    return tuple(ordered if limit is None else ordered[:limit])


def _sorted_resolved_records(
    *,
    records: tuple[FindingRecord, ...],
    limit: int | None,
) -> list[FindingRecord]:
    """Return resolved records newest-first, optionally truncated.

    Args:
        records: Every tracked finding record.
        limit: Maximum number to return, or ``None`` for all.

    Returns:
        Resolved records, most recently fixed first so pruning drops the
        oldest history.
    """
    ordered = sorted(
        (record for record in records if record.status is FindingStatus.RESOLVED),
        key=lambda record: (-record.resolved_round, *_record_sort_key(record)),
    )
    return ordered if limit is None else ordered[:limit]


def _delta_cell(*, record: FindingRecord, match: FindingMatchResult) -> str:
    """Render the ``Δ`` cell for one open finding.

    Args:
        record: Open finding record.
        match: Cross-round matching outcome for this round.

    Returns:
        ``**new**``, ``↩ regressed``, or ``—`` for an unchanged finding.
    """
    outcome = match.outcome_for(record=record)
    if outcome is FindingMatchOutcome.NEW:
        return "**new**"
    if outcome is FindingMatchOutcome.REGRESSED:
        return "↩ regressed"
    return "—"


def _finding_cell(
    *,
    record: FindingRecord,
    repo: str,
    pr_number: int | None,
) -> str:
    """Render the title cell, linked to the finding's inline comment.

    Args:
        record: Open finding record.
        repo: ``owner/name`` slug of the repository.
        pr_number: Pull request number.

    Returns:
        The title as a Markdown link to its thread, or plain text when the
        finding has no inline comment (it was never diff-mappable, the posting
        failed, or its id has not been captured yet). Link syntax inside the
        title is neutralized by ``_cell``'s sanitizer, so a model-written
        ``]`` cannot break out of the link label.
    """
    title = _cell(text=record.title, limit=_TITLE_LIMIT)
    url = inline_comment_url(
        repo=repo,
        pr_number=pr_number,
        comment_id=record.inline_comment_id,
    )
    if not url:
        return title
    return f"[{title.replace('[', '(').replace(']', ')')}]({url})"


def _severity_cell(*, record: FindingRecord) -> str:
    """Render the severity cell for a finding record."""
    if record.is_question:
        return f"{_QUESTION_EMOJI} question"
    return f"{_SEVERITY_EMOJI[record.severity]} {record.severity.value}"


def _location(*, record: FindingRecord) -> str:
    """Render a record's ``file:line`` label for a table cell."""
    path = _cell(text=record.file or "(unknown)", limit=120)
    return f"{path}:{record.line}" if record.line > 0 else path


def _cell(*, text: str, limit: int) -> str:
    """Sanitize model text for safe rendering inside a Markdown table cell.

    Args:
        text: Raw model-derived text.
        limit: Maximum length before truncation.

    Returns:
        Text with mentions neutralized, pipes escaped, and newlines collapsed
        so a single cell cannot break the table it sits in.
    """
    safe = sanitize_comment_text(text, limit=limit)
    return safe.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def _inline_safe(*, text: str, limit: int) -> str:
    """Sanitize model text for embedding *inside* a collapsible.

    On top of the usual mention neutralization, a model-written ``<details>``
    or ``</details>`` is defanged: the folded finding detail lives inside a
    collapsible, so an unescaped closing tag would end it early and let the
    rest of the comment render at the wrong nesting level.

    Args:
        text: Raw model-derived text.
        limit: Maximum length before truncation.

    Returns:
        Text safe to embed within a ``<details>`` block.
    """
    safe = sanitize_comment_text(text, limit=limit)
    return _DETAILS_TAG_RE.sub(r"&lt;\1\2", safe)


def _short_sha(*, sha: str) -> str:
    """Return the display-length prefix of a commit sha, or an empty string."""
    cleaned = sanitize_comment_text(sha, limit=64).strip()[:SHORT_SHA_LENGTH]
    # Escape *after* truncating: escaping first could cut an escape pair in half.
    return cleaned.replace("|", "\\|")


def _transport_label(*, transport: str, auth_mode: str) -> str:
    """Render the transport badge value, never implying a billed amount."""
    parts = [
        sanitize_comment_text(part, limit=40)
        for part in (transport, auth_mode)
        if part.strip()
    ]
    return " · ".join(parts) if parts else "unknown"


def _model_counts(*, runs: list[RunRecord]) -> list[tuple[str, int]]:
    """Count runs per model, sorted by model name.

    Args:
        runs: Run records to count over.

    Returns:
        ``(model, count)`` pairs in stable alphabetical order.
    """
    counts: dict[str, int] = {}
    for run in runs:
        model = run.model or "unknown"
        counts[model] = counts.get(model, 0) + 1
    return sorted(counts.items())


def _fmt_compact(*, value: int) -> str:
    """Format a large count compactly, for example ``24.9k`` or ``1.5M``.

    Args:
        value: Count to format.

    Returns:
        The compact representation. Cumulative token totals across many rounds
        reach seven figures, which must not render as ``1500.0k``.
    """
    if value < 1000:
        return str(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value / 1000:.1f}k"


def _plural(*, count: int, noun: str) -> str:
    """Return ``noun`` pluralized for ``count``."""
    return noun if count == 1 else f"{noun}s"


# --- state -------------------------------------------------------------------


def _state_from_runs(prior_runs: list[dict[str, Any]] | None) -> ReviewState:
    """Build a state object from legacy ``prior_runs`` mappings.

    Args:
        prior_runs: Run mappings recovered from a previous sticky comment, or
            ``None``.

    Returns:
        A state carrying those runs and no finding history.
    """
    runs = tuple(RunRecord.from_dict(run) for run in prior_runs or [])
    return ReviewState(runs=renumber_if_legacy_v1(runs=runs))


def _run_record(
    *,
    result: ReviewResult,
    round_number: int,
    head_sha: str,
    transport: str,
    auth_mode: str,
    cost_basis: str,
    verdict: ReviewVerdict,
    resolved: int,
    open_after: int,
) -> RunRecord:
    """Build a machine-readable run record from a review result.

    Args:
        result: Current review result.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        cost_basis: Provenance of the reported cost.
        verdict: Readiness verdict derived from the open findings.
        resolved: Number of findings this round resolved.
        open_after: Number of findings still open after this round.

    Returns:
        The run record persisted in the state blob.
    """
    metadata = result.metadata
    counts = _severity_counts(findings=result.findings)
    usage = metadata.token_usage
    effective_auth = auth_mode or metadata.auth_mode
    effective_basis = cost_basis or metadata.cost_basis
    if not effective_basis:
        # Stamp provenance at creation so a fresh render and a re-render of
        # parsed state serialize identically (parse derives the same value
        # for legacy blobs; without this, an error-path re-render would
        # rewrite the blob a "failed round persists state untouched"
        # consumer expects byte-for-byte).
        derived = resolve_cost_basis(
            auth_mode=effective_auth,
            estimated=bool(metadata.token_usage_estimated),
        )
        if derived is None:
            logger.debug(
                "cost_basis derivation returned no value for "
                f"auth_mode={effective_auth!r}; run record keeps an empty "
                "basis (unrecognized auth mode).",
            )
        effective_basis = derived.value if derived is not None else ""
    return RunRecord(
        round=round_number,
        timestamp=metadata.timestamp,
        sha=head_sha,
        model=metadata.model,
        provider=metadata.provider,
        transport=transport or metadata.transport,
        auth_mode=effective_auth,
        cost_basis=effective_basis,
        depth=metadata.depth,
        strictness=metadata.strictness,
        files_reviewed=metadata.files_reviewed,
        files_skipped=max(metadata.files_total - metadata.files_reviewed, 0),
        checks=metadata.checklist_items,
        duration=metadata.duration_seconds,
        prompt=int(usage.get("prompt", 0)),
        completion=int(usage.get("completion", 0)),
        total=int(usage.get("total", 0)),
        cost=metadata.cost_estimate_usd,
        estimated=bool(metadata.token_usage_estimated),
        verdict=verdict,
        p1=counts[Severity.P1],
        p2=counts[Severity.P2],
        p3=counts[Severity.P3],
        questions=sum(1 for finding in result.findings if finding.is_question),
        downgraded=count_downgrades(findings=result.findings),
        partial=bool(metadata.partial),
        coverage_limited=not metadata.findings_coverage_complete,
        chunks_reviewed=metadata.chunks_reviewed,
        chunks_total=metadata.chunks_total,
        resolved=resolved,
        open_after=open_after,
        narrative=_round_narrative(result=result),
    )


def _round_narrative(*, result: ReviewResult) -> str:
    """Extract the one-line narrative persisted for this round.

    Args:
        result: Current review result.

    Returns:
        The structured summary's headline when the model produced one, else the
        first sentence of the flat summary, else an empty string. Only the
        first sentence is kept: the recap is one line under a round heading,
        and a paragraph there turns the history into the wall of text the
        sticky redesign exists to undo.
    """
    summary = result.pr_summary
    headline = (summary.headline if summary else "").strip()
    text = headline or result.summary.strip()
    if not text:
        return ""
    # Whitespace is normalized first so a sentence broken across lines is still
    # recognized as one boundary, and so the stored line cannot carry a newline
    # into the recap.
    normalized = " ".join(text.split())
    sentence = _SENTENCE_BOUNDARY_RE.split(normalized, maxsplit=1)[0]
    return sentence[:_NARRATIVE_LIMIT].strip()


def _cap_body(*, body: str, reserved: int = 0) -> str:
    """Hard-truncate an over-long body as the final size safety net.

    Section-aware pruning in :func:`_fit_body` handles every realistic
    overflow. This exists so a pathological single section (one enormous
    finding title, say) can still never produce a comment GitHub rejects.
    ``reserved`` leaves room for the trailing state block (#1866).

    Args:
        body: Sticky comment body without the state block.
        reserved: Characters already claimed by the trailing state block.

    Returns:
        The body unchanged when it fits, else truncated with a visible notice.
    """
    limit = _body_char_limit(reserved=reserved)
    if len(body) <= limit:
        return body
    notice = "\n\n> ✂️ Comment truncated to fit GitHub's size limit."
    keep = max(limit - len(notice), 0)
    return body[:keep].rstrip() + notice


def parse_review_state(*, body: str) -> list[dict[str, Any]]:
    """Extract prior run records from a sticky comment's state block.

    Compatibility wrapper over :func:`parse_review_state_v2` for callers that
    only need the run history as plain mappings.

    Args:
        body: Existing sticky comment body.

    Returns:
        List of run records, or an empty list when no valid state is present.
    """
    return [run.to_dict() for run in parse_review_state_v2(body=body).runs]


def parse_review_state_v2(*, body: str) -> ReviewState:
    """Decode the full v2 review state from a sticky comment's state block.

    v1 blobs are migrated in place; a missing, malformed, or unknown-version
    blob yields an empty state rather than raising.

    Args:
        body: Existing sticky comment body.

    Returns:
        The decoded review state.
    """
    return decode_state(body=body)
