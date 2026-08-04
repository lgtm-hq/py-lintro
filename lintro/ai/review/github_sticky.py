"""Sticky-comment assembly, state, and size capping for GitHub reviews.

The sticky comment is the PR's *mission control* (#1909, epic #1905): a living
status board edited in place on every run. It leads with the derived readiness
verdict and the round-over-round delta, then indexes the open findings — it
deliberately does **not** repeat the finding detail that already lives on the
inline comments.

Layout, top to bottom:

1. header — ``🔎 Lintro Review · round N · commit <sha>``
2. readiness pill + delta line
3. ``Summary`` — headline plus walkthrough bullets, severity-marked when a
   bullet is tied to an open P1/P2
4. ``Why it's blocked`` — the model's reasoning, the verdict rubric as
   fine-print, and the files needing attention
5. severity tiles (blockers / warnings / nits / fixed)
6. ``Open findings`` — one line per finding, titles only
7. the fix-all agent prompt panel, scoped to *all* still-open findings
8. ``Resolved`` — struck-through titles with their fixing commit
9. *This run* badges, two lines (model-first ordering)
10. ``---`` then exactly one ``🕘 Run history`` collapsible
11. a one-line footer

Two invariants the renderer enforces:

* **No nested ``<details>``.** Every collapsible is top level; the run history
  carries plain tables and the degraded fold-in flattens finding detail.
* **The comment always fits GitHub's 65,536-char cap.** Oldest run history is
  pruned first, then resolved findings, then open findings — each with a
  visible marker, never a silent drop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

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
from lintro.ai.review.finding_matcher import derive_verdict, match_findings
from lintro.ai.review.github_constants import (
    _SEVERITY_EMOJI,
    MAX_COMMENT_CHARS,
    MAX_STORED_RUNS,
    SHORT_SHA_LENGTH,
    STICKY_FOOTER,
    STICKY_MARKER,
)
from lintro.ai.review.github_render import (
    _fmt_cost,
    _fmt_int,
    _format_checklist_appendix_markdown,
    _severity_counts,
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
from lintro.ai.review.review_state_codec import (
    decode_state,
    prune_state_to_fit,
    render_state_block,
    renumber_if_legacy_v1,
)
from lintro.ai.review.severity_gate import count_downgrades
from lintro.ai.review.verdict import (
    VERDICT_RUBRIC_FINE_PRINT,
    resolve_bullet_finding,
    verdict_label,
)

__all__ = [
    "build_sticky_comment",
    "parse_review_state",
    "parse_review_state_v2",
]

#: Emoji rendered next to each readiness verdict's label.
VERDICT_EMOJI: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "⛔",
    ReviewVerdict.CHANGES_REQUESTED: "⚠️",
    ReviewVerdict.NITS_ONLY: "🟡",
    ReviewVerdict.READY: "✅",
}

#: Heading used for the reasoning section, per verdict.
_REASONING_HEADINGS: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "Why it's blocked",
    ReviewVerdict.CHANGES_REQUESTED: "Why changes are requested",
    ReviewVerdict.NITS_ONLY: "Why it's flagged",
    ReviewVerdict.READY: "Why it's ready",
}

#: Noun naming the finding class that decides each verdict, for the pill.
_VERDICT_NOUNS: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "blocker",
    ReviewVerdict.CHANGES_REQUESTED: "warning",
    ReviewVerdict.NITS_ONLY: "nit",
    ReviewVerdict.READY: "finding",
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

#: Emoji marking a tracked entry that is a question rather than a finding.
_QUESTION_EMOJI = "❓"

#: Maximum characters of a finding title rendered in a table cell.
_TITLE_LIMIT = 160

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
    inline_failure: InlinePostFailure | None = None,
) -> str:
    """Compose the full v5 "mission control" sticky PR comment body.

    This round's findings are matched against the prior state, so the rendered
    delta, the ``since`` column, and the persisted v2 blob all agree on each
    finding's identity, first-seen round, and resolution provenance.

    Args:
        result: Current review result.
        prior_runs: Legacy run records recovered from the previous sticky
            comment's state block. Ignored when ``prior_state`` is given.
        prior_state: Full state decoded from the previous sticky comment.
            ``None`` for the first run on a PR.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for the checklist appendix and
            for linked questions on folded-in finding detail.
        diff_lines: Diff line map. Retained for interface compatibility with
            the inline-posting path; the v5 sticky indexes every open finding
            identically whether or not it also posts inline.
        head_sha: Head commit sha reviewed in this round; stamped onto findings
            resolved by this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        inline_failure: Findings whose inline comments could not be posted.
            When set, the sticky renders a warning row above the open-findings
            table and folds those findings' full detail back in.

    Returns:
        Complete Markdown body carrying the hidden marker and state block,
        guaranteed to fit GitHub's comment size limit.
    """
    del diff_lines  # Interface compatibility; the v5 sticky indexes uniformly.
    state = prior_state if prior_state is not None else _state_from_runs(prior_runs)
    round_number = state.next_round
    match = match_findings(
        previous=state,
        findings=result.findings,
        round_number=round_number,
        head_sha=head_sha,
    )
    verdict = derive_verdict(findings=match.records)
    prior = list(state.runs)
    current = _run_record(
        result=result,
        round_number=round_number,
        head_sha=head_sha,
        transport=transport,
        auth_mode=auth_mode,
        verdict=verdict,
    )
    combined_runs = [*prior, current]
    all_runs = combined_runs[-MAX_STORED_RUNS:]
    runs_dropped = len(all_runs) < len(combined_runs)

    def assemble(*, limits: _RenderLimits) -> str:
        """Render the whole body at the given per-section limits."""
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
        )

    body = _fit_body(assemble=assemble, prior_run_count=len(all_runs) - 1)
    new_state = ReviewState(
        runs=tuple(all_runs),
        findings=match.records,
        truncated=state.truncated or runs_dropped,
    )
    return body + render_state_block(
        state=prune_state_to_fit(state=new_state, body=body),
    )


def _fit_body(
    *,
    assemble: _Assembler,
    prior_run_count: int,
) -> str:
    """Shrink the rendered body until it fits ``MAX_COMMENT_CHARS``.

    Pruning order is deliberate: history is the least valuable content on the
    comment, resolved findings are already fixed, and open findings are what a
    reader is actually here for, so they are trimmed last. Each stage leaves a
    visible marker, so nothing is ever dropped silently.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        prior_run_count: Number of prior runs available to the history table.

    Returns:
        A body at or under the cap when that is reachable by pruning, else the
        smallest body pruning can produce, hard-truncated as a last resort.
    """
    limits = _RenderLimits()
    body = assemble(limits=limits)
    if len(body) <= MAX_COMMENT_CHARS:
        return body

    # 1. Drop the oldest run history first, one round at a time.
    for history in range(prior_run_count - 1, -1, -1):
        limits = replace(limits, history=history)
        body = assemble(limits=limits)
        if len(body) <= MAX_COMMENT_CHARS:
            return body

    # 2. Then the oldest resolved findings — they are already fixed.
    fitted = _largest_fitting(assemble=assemble, limits=limits, field="resolved")
    if fitted is not None:
        return fitted

    # 3. Finally the open findings, keeping as many as fit. A verdict with no
    # substance is worse than an over-long comment the final cap will trim, so
    # one finding is always rendered even when it alone overflows.
    limits = replace(limits, resolved=0)
    fitted = _largest_fitting(assemble=assemble, limits=limits, field="open")
    if fitted is None:
        fitted = assemble(limits=replace(limits, open=1))
    return _cap_body(body=fitted)


def _largest_fitting(
    *,
    assemble: _Assembler,
    limits: _RenderLimits,
    field: str,
) -> str | None:
    """Binary-search the largest value of one limit whose body still fits.

    Both prunable finding sections order newest-first, so capping their count
    drops the oldest entries — the same oldest-first policy the run history
    follows.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        limits: Limits already applied to the cheaper sections.
        field: Name of the :class:`_RenderLimits` field to search over.

    Returns:
        The body rendered at the largest fitting count, or ``None`` when not
        even zero entries of that section make the body fit.
    """
    best: str | None = None
    lower, upper = 0, _PRUNE_SEARCH_CEILING
    while lower <= upper:
        middle = (lower + upper) // 2
        candidate = assemble(limits=replace(limits, **{field: middle}))
        if len(candidate) <= MAX_COMMENT_CHARS:
            best = candidate
            lower = middle + 1
        else:
            upper = middle - 1
    return best


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
) -> str:
    """Render every sticky section in order and join the non-empty ones.

    Args:
        result: Current review result.
        match: Cross-round matching outcome for this round.
        verdict: Readiness verdict derived from the open findings.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.
        runs: Every retained run record, oldest first, current run last.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text.
        inline_failure: Findings whose inline comments could not be posted.
        limits: Per-section render limits.

    Returns:
        The assembled body, without the hidden state block.
    """
    open_records = _sorted_open_records(records=match.records, limit=limits.open)
    open_findings = _sorted_open_findings(
        findings=result.findings,
        limit=limits.open,
    )
    resolved_records = _sorted_resolved_records(
        records=match.records,
        limit=limits.resolved,
    )
    total_open = len(_sorted_open_records(records=match.records, limit=None))
    total_resolved = len(_sorted_resolved_records(records=match.records, limit=None))

    sections: list[str] = [
        STICKY_MARKER,
        _header(round_number=round_number, head_sha=head_sha),
        _readiness_pill(verdict=verdict, records=match.records),
        _delta_line(match=match, round_number=round_number),
        _summary_section(result=result),
        _reasoning_section(result=result, verdict=verdict),
        _tiles_section(records=match.records),
        _degraded_row(failure=inline_failure),
        _open_findings_section(
            records=open_records,
            match=match,
            total=total_open,
        ),
        _degraded_details(
            failure=inline_failure,
            checklist_display=checklist_display,
            question_map=question_map,
        ),
        render_agent_prompt_panel(
            findings=open_findings,
            scope=AgentPromptScope(
                kind=AgentPromptScopeKind.ALL_OPEN,
                round_number=round_number,
            ),
        ),
        _resolved_section(records=resolved_records, total=total_resolved),
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
    )
    if history:
        sections.extend(["---", history])
    sections.append(STICKY_FOOTER)
    return "\n\n".join(section for section in sections if section)


# --- section renderers -------------------------------------------------------


def _header(*, round_number: int, head_sha: str) -> str:
    """Render the sticky comment's title line.

    Args:
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round, possibly empty.

    Returns:
        The Markdown heading line.
    """
    parts = ["## 🔎 Lintro Review", f"round {round_number}"]
    short = _short_sha(sha=head_sha)
    if short:
        parts.append(f"commit `{short}`")
    return " · ".join(parts)


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
    unchanged = len(match.carried) + len(match.regressed)
    return (
        f"✔ {len(match.resolved)} resolved · **{len(match.new)} new** · "
        f"{unchanged} unchanged since round {round_number - 1}"
    )


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
    """Render the model's verdict reasoning plus the derivation fine-print.

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
    lines.extend(["", f"<sub>{VERDICT_RUBRIC_FINE_PRINT}</sub>"])
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
        if record.status is FindingStatus.RESOLVED:
            fixed += 1
            continue
        if record.is_question:
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
        f"{cause}. Full details are folded in below until inline posting "
        "succeeds."
    )


def _open_findings_section(
    *,
    records: list[FindingRecord],
    match: FindingMatchResult,
    total: int,
) -> str:
    """Render the open-findings index table.

    Titles only, one line each: the detail lives on the inline comments, and
    duplicating it here is what made the previous sticky unreadable.

    Args:
        records: Open records to render, already ordered and limited.
        match: Cross-round matching outcome, for the ``Δ`` column.
        total: Total number of open findings before any limit was applied.

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
            f"| {_cell(text=record.title, limit=_TITLE_LIMIT)} "
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
) -> str:
    """Fold full finding detail into the sticky when inline posting failed.

    Rendered flat inside a single ``<details>``: the sticky's no-nesting rule
    means this cannot reuse the inline comment renderer, which carries its own
    collapsible.

    Args:
        failure: Findings whose inline comments could not be posted.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked questions.

    Returns:
        A single-level collapsible carrying each failed finding's detail, or an
        empty string when inline posting succeeded.
    """
    if failure is None or failure.is_empty:
        return ""

    lines = [
        f"<details><summary>📋 Details for {failure.count} "
        f"{_plural(count=failure.count, noun='finding')} not posted inline"
        "</summary>",
        "",
    ]
    for finding in failure.findings:
        lines.extend(
            _folded_finding(
                finding=finding,
                checklist_display=checklist_display,
                question_map=question_map,
            ),
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
    location = sanitize_comment_text(finding.file, limit=200)
    where = f"`{location}:{finding.line}`" if finding.line > 0 else f"`{location}`"
    lines = [
        f"**{emoji} {label}** · `{sanitize_comment_text(finding.category, limit=60)}`"
        f" — **{sanitize_comment_text(finding.title, limit=_TITLE_LIMIT)}** · {where}",
        "",
        sanitize_comment_text(finding.description, limit=2000),
    ]
    for heading, text in (("Cause", finding.cause), ("Fix", finding.fix)):
        body = sanitize_comment_text(text, limit=2000).strip()
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
    """Render the two-line badge block for the current run.

    Ordering is fixed across every surface (epic #1905): model, est. cost,
    tokens in, tokens out on line 1; transport and mechanics on line 2. No
    figure is presented as billed — the ``transport`` badge and the ``~``
    prefix carry that honesty.

    Args:
        result: Current review result.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.

    Returns:
        The ``This run`` section.
    """
    metadata = result.metadata
    estimated = metadata.token_usage_estimated
    prefix = "~" if estimated else ""
    usage = metadata.token_usage
    first = " · ".join(
        [
            f"model `{sanitize_comment_text(metadata.model, limit=60)}`",
            f"est. cost `{_fmt_cost(metadata.cost_estimate_usd, estimated=estimated)}`",
            f"tokens in `{prefix}{_fmt_int(int(usage.get('prompt', 0)))}`",
            f"tokens out `{prefix}{_fmt_int(int(usage.get('completion', 0)))}`",
        ],
    )
    second = " · ".join(
        [
            f"transport `{_transport_label(transport=transport, auth_mode=auth_mode)}`",
            f"depth `{metadata.depth}`",
            f"files `{metadata.files_reviewed}`",
            f"checks `{metadata.checklist_items}`",
            f"duration `{metadata.duration_seconds:.0f}s`",
        ],
    )
    return f"**This run** — {first}\n\n{second}"


def _history_section(
    *,
    runs: list[RunRecord],
    limit: int | None,
    resolved_total: int,
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
    models = _model_counts(runs=runs)

    shown = runs if limit is None else [*runs[:-1][len(runs) - 1 - limit :], runs[-1]]
    dropped = len(runs) - len(shown)

    summary = (
        f"🕘 Run history — {len(runs)} runs · "
        f"{_fmt_cost(total_cost, estimated=estimated)} · "
        f"{prefix}{_fmt_compact(value=total_tokens)} tokens · "
        f"{len(models)} {_plural(count=len(models), noun='model')}"
    )
    badges = " · ".join(
        [
            "models "
            + " ".join(
                f"`{sanitize_comment_text(model, limit=60)}` ×{count}"
                for model, count in models
            ),
            f"est. cost {_fmt_cost(total_cost, estimated=estimated)}",
            f"tokens {prefix}{_fmt_int(total_tokens)} "
            f"(in {prefix}{_fmt_int(sum(run.prompt for run in runs))} / "
            f"out {prefix}{_fmt_int(sum(run.completion for run in runs))})",
            f"findings {sum(run.p1 + run.p2 + run.p3 for run in runs)} raised · "
            f"{resolved_total} resolved",
        ],
    )

    lines = [
        f"<details><summary>{summary}</summary>",
        "",
        badges,
        "",
        "| Run | Commit | Verdict | Model | Open | Tokens (in/out) | Est. cost "
        "| Duration |",
        "|:-:|---|---|---|:-:|---|---|---|",
    ]
    for run in reversed(shown):
        lines.append(_history_row(run=run, latest=run is runs[-1]))
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} older "
                f"{_plural(count=dropped, noun='run')} not listed** "
                "(history truncated to fit GitHub's size limit).",
            ],
        )
    lines.append("")
    lines.extend(_history_mini_summary(run=run) for run in reversed(shown[:-1]))
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def _history_row(*, run: RunRecord, latest: bool) -> str:
    """Render one row of the per-run history table.

    Args:
        run: Run record to render.
        latest: True when this is the most recent run.

    Returns:
        A single Markdown table row.
    """
    prefix = "~" if run.estimated else ""
    short = _short_sha(sha=run.sha)
    return (
        f"| {run.round}{' (latest)' if latest else ''} "
        f"| {f'`{short}`' if short else '—'} "
        f"| {VERDICT_EMOJI[run.verdict]} {verdict_label(verdict=run.verdict).lower()} "
        f"| `{_cell(text=run.model or 'unknown', limit=60)}` "
        f"| {run.p1 + run.p2 + run.p3} "
        f"| {prefix}{_fmt_int(run.prompt)} / {prefix}{_fmt_int(run.completion)} "
        f"| {_fmt_cost(run.cost, estimated=run.estimated)} "
        f"| {run.duration:.0f}s |"
    )


def _history_mini_summary(*, run: RunRecord) -> str:
    """Render one prior round's one-line recap under the history table.

    Args:
        run: Prior run record to summarize.

    Returns:
        A single Markdown line.
    """
    short = _short_sha(sha=run.sha)
    where = f" · `{short}`" if short else ""
    return (
        f"**Round {run.round}**{where} · "
        f"{VERDICT_EMOJI[run.verdict]} {verdict_label(verdict=run.verdict).lower()} — "
        f"🔴 {run.p1} · 🟠 {run.p2} · 🟡 {run.p3}"
        + (" · ⚠️ partial" if run.partial else "")
    )


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
    ordered = sorted(
        findings,
        key=lambda finding: (finding.severity.value, finding.file, finding.line),
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


def _short_sha(*, sha: str) -> str:
    """Return the display-length prefix of a commit sha, or an empty string."""
    cleaned = sanitize_comment_text(sha, limit=64).strip()
    return cleaned[:SHORT_SHA_LENGTH]


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
    """Format a large count compactly, for example ``24.9k``."""
    if value < 1000:
        return str(value)
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
    verdict: ReviewVerdict,
) -> RunRecord:
    """Build a machine-readable run record from a review result.

    Args:
        result: Current review result.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        verdict: Readiness verdict derived from the open findings.

    Returns:
        The run record persisted in the state blob.
    """
    metadata = result.metadata
    counts = _severity_counts(findings=result.findings)
    usage = metadata.token_usage
    return RunRecord(
        round=round_number,
        timestamp=metadata.timestamp,
        sha=head_sha,
        model=metadata.model,
        provider=metadata.provider,
        transport=transport,
        auth_mode=auth_mode,
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
        chunks_reviewed=metadata.chunks_reviewed,
        chunks_total=metadata.chunks_total,
    )


def _cap_body(*, body: str) -> str:
    """Hard-truncate an over-long body as the final size safety net.

    Section-aware pruning in :func:`_fit_body` handles every realistic
    overflow. This exists so a pathological single section (one enormous
    finding title, say) can still never produce a comment GitHub rejects.

    Args:
        body: Sticky comment body without the state block.

    Returns:
        The body unchanged when it fits, else truncated with a visible notice.
    """
    if len(body) <= MAX_COMMENT_CHARS:
        return body
    notice = "\n\n> ✂️ Comment truncated to fit GitHub's size limit."
    keep = MAX_COMMENT_CHARS - len(notice)
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
