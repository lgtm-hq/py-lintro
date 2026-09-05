"""Finding and summary rendering for GitHub AI-review comments."""

from __future__ import annotations

import re
from collections.abc import Sequence

from lintro.ai.resolved_ai_config import (
    MAX_COST_LABEL,
    format_max_cost_label,
    format_sourced_value,
)
from lintro.ai.review.agent_prompts import render_finding_prompt_panel
from lintro.ai.review.checklist_display import (
    cleared_answers,
    format_review_questions_markdown,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.convergence import (
    format_convergence_stamp,
    format_score,
    format_trajectory,
)
from lintro.ai.review.coverage_degradation import (
    COVERAGE_LIMITED_HEADLINE,
    describe_coverage_degradations,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.cross_chunk_contradiction import CrossChunkContradiction
from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
from lintro.ai.review.github_constants import _MENTION_RE, _SEVERITY_EMOJI
from lintro.ai.review.inline_fix import InlineFixPlan, normalize_diff_path
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.sanitize import sanitize_comment_text
from lintro.ai.review.severity_gate import describe_cross_chunk_contradictions
from lintro.ai.review.synthesis_note import format_synthesis_note
from lintro.ai.review.timings import format_timing_summary

__all__ = [
    "REGRESSED_TITLE_SUFFIX",
    "format_convergence_banner",
    "format_convergence_note",
    "format_coverage_limited_warning",
    "format_cross_chunk_note",
    "format_inline_post_cause",
    "format_inline_post_note",
    "format_synthesis_note_line",
    "format_timings_note",
    "sanitized_timing_summary",
    "format_badge_table",
    "format_badge_tables",
    "format_finding_comment",
    "format_run_mechanics",
    "run_stats_primary_cells",
    "sanitize_comment_text",
]

#: Appended to the title of a regression's freshly raised inline comment, so
#: the thread does not read as a brand-new finding.
REGRESSED_TITLE_SUFFIX = " (regressed)"

#: Severities that earn a per-finding agent prompt panel (#1911). A P3 nit gets
#: none: the panel is an affordance, and one on every finding is wallpaper.
_PROMPT_SEVERITIES: frozenset[Severity] = frozenset({Severity.P1, Severity.P2})

#: Line breaks that would end a badge-table row early. ``\r\n`` is matched as
#: one break so a Windows-style value collapses to a single space, not two.
_LINE_BREAK_RE = re.compile(r"\r\n|[\r\n]")


def _chip(text: str) -> str:
    """Render a value as an inline code chip, escaping backticks."""
    safe = sanitize_comment_text(text, limit=60).replace("`", "'")
    return f"`{safe}`"


def _severity_badge(*, severity: Severity) -> str:
    """Render a severity as a color emoji plus bold label."""
    emoji = _SEVERITY_EMOJI.get(severity, "⚪")
    return f"{emoji} **{severity.value}**"


def _fmt_int(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def _fmt_cost(value: float, *, estimated: bool) -> str:
    """Format a USD cost, prefixing ``~`` when the value is estimated."""
    prefix = "~" if estimated else ""
    return f"{prefix}${value:.4f}"


def _fmt_tokens(total: int, *, estimated: bool) -> str:
    """Format a token count, prefixing ``~`` when estimated."""
    prefix = "~" if estimated else ""
    return f"{prefix}{_fmt_int(total)} tok"


def _escape_cell(text: str) -> str:
    r"""Flatten and escape a badge-table cell so it cannot shear the row.

    A table row is one line, so a carriage return or line feed in a value ends
    the row and spills the rest of the cells into the document as prose. Line
    breaks are therefore collapsed to spaces before escaping — GFM offers no
    in-cell line break worth preserving here, and ``sanitize_comment_text``
    caps length without touching them.

    Backslashes are doubled first: escaping only the pipe would turn an input
    of ``\|`` into ``\\|``, leaving the pipe with an even number of
    preceding backslashes and readable as a delimiter again.
    """
    escaped = text.replace("\\", "\\\\").replace("|", "\\|")
    return _LINE_BREAK_RE.sub(" ", escaped)


def format_badge_table(*, cells: Sequence[tuple[str, str]]) -> list[str]:
    r"""Render one ordered row of ``(label, value)`` pairs as a badge table.

    GitHub-flavored Markdown has no chip primitive, so a single-row table —
    labels as the header, values as the one body row — is the closest thing to
    the approved chip design that renders without an external image.

    A literal ``|`` would end the cell it appears in and shear the row, so it
    is escaped here rather than at each call site — GFM honors ``\|`` inside
    code spans too, which the code-chipped values rely on. Callers still own
    their own sanitization and code-chip quoting.

    Args:
        cells: Ordered ``(label, value)`` pairs.

    Returns:
        Markdown lines, or an empty list when there is nothing to render.
    """
    if not cells:
        return []
    keys = " | ".join(_escape_cell(key) for key, _ in cells)
    dividers = " | ".join("---" for _ in cells)
    values = " | ".join(_escape_cell(value) for _, value in cells)
    return [f"| {keys} |", f"| {dividers} |", f"| {values} |"]


def format_badge_tables(
    *,
    rows: Sequence[Sequence[tuple[str, str]]],
) -> list[str]:
    """Render several badge rows as stacked single-row tables.

    Args:
        rows: Ordered row groups, each an ordered list of ``(label, value)``
            pairs. Empty groups are skipped rather than emitting a blank table.

    Returns:
        Markdown lines with one blank line between consecutive tables.
    """
    lines: list[str] = []
    for cells in rows:
        table = format_badge_table(cells=cells)
        if not table:
            continue
        if lines:
            lines.append("")
        lines.extend(table)
    return lines


def run_stats_primary_cells(*, metadata: ReviewMetadata) -> list[tuple[str, str]]:
    """Build the primary run-stats badge row shared by every review surface.

    Ordering is fixed across surfaces (epic #1905): model, est. cost, tokens
    in, tokens out. ``~`` marks values estimated locally, so a subscription run
    never presents an estimate as a billed figure.

    Args:
        metadata: Review run metadata.

    Returns:
        Ordered ``(label, value)`` pairs for the primary badge table.
    """
    estimated = metadata.token_usage_estimated
    tilde = "~" if estimated else ""
    prompt_tokens = int(metadata.token_usage.get("prompt", 0))
    completion_tokens = int(metadata.token_usage.get("completion", 0))
    return [
        (
            "model",
            format_sourced_value(
                f"`{sanitize_comment_text(metadata.model, limit=60)}`",
                metadata.model_source or None,
            ),
        ),
        ("est. cost", _fmt_cost(metadata.cost_estimate_usd, estimated=estimated)),
        ("tokens in", f"{tilde}{_fmt_int(prompt_tokens)}"),
        ("tokens out", f"{tilde}{_fmt_int(completion_tokens)}"),
    ]


def sanitized_timing_summary(*, metadata: ReviewMetadata) -> str:
    """Return the per-phase timing summary, sanitized for a posted comment.

    The single cap for every GitHub surface, so a later change cannot clip
    one comment and not another. The text is trusted instrumentation, not
    model prose: the cap only bounds a pathological run.

    Args:
        metadata: Review run metadata.

    Returns:
        The sanitized summary, or an empty string when the run was not
        instrumented.
    """
    if metadata.timings is None:
        return ""
    return sanitize_comment_text(
        format_timing_summary(timings=metadata.timings),
        limit=1000,
    )


def format_timings_note(*, metadata: ReviewMetadata) -> str:
    """Render the per-phase timing summary as a small note for posted comments.

    Shared by every success surface that shows run mechanics (the review
    body's run-stats block and the sticky's ``This run`` table) so the posted
    comment carries the same one-line breakdown the terminal prints (#2148).

    Args:
        metadata: Review run metadata.

    Returns:
        A ``<sub>`` line with the summary, or an empty string when the run was
        not instrumented.
    """
    summary = sanitized_timing_summary(metadata=metadata)
    return f"<sub>Timings: {summary}</sub>" if summary else ""


def format_synthesis_note_line(*, metadata: ReviewMetadata) -> str:
    """Render the cross-chunk synthesis note as a small note for comments.

    Shares its wording with the terminal through
    :func:`format_synthesis_note`, so the posted comment can never describe
    the extra pass differently from the run that produced it (#2269).

    Args:
        metadata: Review run metadata.

    Returns:
        A ``<sub>`` line describing the pass, or an empty string when the pass
        did not run.
    """
    note = format_synthesis_note(metadata=metadata)
    if not note:
        return ""
    return f"<sub>{sanitize_comment_text(note, limit=400)}</sub>"


def format_coverage_limited_warning(*, metadata: ReviewMetadata) -> str:
    """Render the shared coverage-limited warning for posted GitHub surfaces.

    The review body and the sticky comment both call this, so the two can
    never disagree about whether a run was capped (#2003). It is the sibling
    of the cost-cap ``partial`` warning and carries equal prominence: a capped
    run is *not* a guaranteed full finding set.

    Args:
        metadata: Review run metadata.

    Returns:
        A blockquote warning, or an empty string when coverage was complete.
    """
    detail = describe_coverage_degradations(metadata=metadata)
    if not detail:
        return ""
    return (
        f"> ⚠️ **{COVERAGE_LIMITED_HEADLINE}** — "
        f"{sanitize_comment_text(detail, limit=400)}"
    )


#: Human wording per inline-post failure kind. Only ``LINE_MAPPING`` may say a
#: finding anchors outside the diff: attributing that to a throttled token is
#: exactly the misreport #2266 fixes.
_INLINE_POST_CAUSES: dict[InlinePostFailureKind, str] = {
    InlinePostFailureKind.RATE_LIMITED: "GitHub rate limit",
    InlinePostFailureKind.LINE_MAPPING: (
        "some findings map to no line in this PR's diff"
    ),
    InlinePostFailureKind.PERMISSION: (
        "this token is not permitted to post reviews on this PR"
    ),
    InlinePostFailureKind.OTHER: "the inline review comments could not be posted",
}


def format_inline_post_cause(
    *,
    kind: InlinePostFailureKind,
    status: int | None = None,
) -> str:
    """Render the human cause for a failed or skipped inline post.

    The single source of the wording for every surface that explains why a
    finding has no inline comment: the sticky's degraded row, the reason
    stored on :class:`~lintro.ai.review.models.inline_post_failure.InlinePostFailure`,
    and the JSON payload the CI classifier reads (#2266).

    Args:
        kind: Classified cause of the failure.
        status: HTTP status GitHub answered with, named in the text when
            known.

    Returns:
        A short lowercase phrase, with ``(HTTP <status>)`` appended when a
        status is known.
    """
    cause = _INLINE_POST_CAUSES[kind]
    return f"{cause} (HTTP {status})" if status is not None else cause


def format_inline_post_note(*, failure: InlinePostFailure | None) -> str:
    """Render the warning row shown when findings have no inline comment.

    Args:
        failure: Findings whose inline comments could not be posted.

    Returns:
        A blockquote warning naming the count and the cause, or an empty
        string when every finding reached an inline comment.
    """
    if failure is None or failure.is_empty:
        return ""
    noun = "finding" if failure.count == 1 else "findings"
    surface = "an inline comment" if failure.count == 1 else "inline comments"
    reason = sanitize_comment_text(failure.reason, limit=200).strip()
    cause = f" ({reason})" if reason else ""
    return (
        f"> ⚠️ **{failure.count} {noun} could not be posted as {surface}**"
        f"{cause}. Full details are folded in below instead."
    )


def _cross_chunk_band_clause(*, findings: Sequence[ReviewFinding]) -> str:
    """Describe the severity effect of the tagged findings, if any moved.

    Args:
        findings: Findings after the cross-chunk guard ran.

    Returns:
        ``", one band lower"`` when at least one tagged finding was actually
        downgraded; an empty string when only P3 findings were tagged.
    """
    lowered = any(
        finding.cross_chunk_contradiction
        is CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED
        for finding in findings
    )
    return ", one band lower" if lowered else ""


def format_cross_chunk_note(*, findings: Sequence[ReviewFinding]) -> str:
    """Render the shared cross-chunk downgrade note for posted GitHub surfaces.

    The review body and the sticky comment both call this, so neither can
    describe the guard differently from the other (#2265). The note says what
    was downgraded and why, because the alternative — editing severities the
    model reported and saying nothing — is exactly the silent behavior the
    other no-silent-caps notices exist to prevent.

    Args:
        findings: Findings for the current round.

    Returns:
        A blockquote note naming the count, or an empty string when the guard
        did not fire.
    """
    notice = describe_cross_chunk_contradictions(findings=findings)
    if not notice:
        return ""
    return (
        f"> 🧩 **{sanitize_comment_text(notice, limit=300)}** — chunked review "
        "shows each chunk the other files at the base commit, so the claim is "
        f"chunk-local; the finding is kept"
        f"{_cross_chunk_band_clause(findings=findings)}."
    )


def format_convergence_note(*, trajectory: tuple[float, ...]) -> str:
    """Render the convergence score and its trajectory as a one-line note.

    The single builder for this line, so the mission-control sticky and any
    later surface that shows the stability signal can never disagree about
    how a trajectory reads (#2099). The latest score leads because that is
    the number the stop rule compares; the arrow chain behind it is what
    tells a reader whether the review is settling or still moving.

    Args:
        trajectory: Recorded scores, oldest first.

    Returns:
        A ``<sub>`` line, or an empty string when no round carries a score —
        which is every round persisted before scoring existed.
    """
    if not trajectory:
        return ""
    latest = format_score(score=trajectory[-1])
    if len(trajectory) == 1:
        return f"<sub>Convergence score {latest}</sub>"
    return (
        f"<sub>Convergence score {latest} · trajectory "
        f"{format_trajectory(scores=trajectory)}</sub>"
    )


def format_convergence_banner(
    *,
    decision: ConvergenceDecision,
    open_p1: int = 0,
) -> str:
    """Render the blockquote stamped on the sticky for a short-circuited round.

    Args:
        decision: The converged decision that skipped the round.
        open_p1: Open, non-question P1 findings the last real round left in
            force. Named on the banner when non-zero: the skip does not
            redden the CI check for them (a reviewed round does not either),
            so the board is where a reader has to be able to see that
            something is still outstanding.

    Returns:
        A blockquote naming the round, the score, the threshold, any P1
        findings still open, and how to force a round anyway.
    """
    noun = "finding" if open_p1 == 1 else "findings"
    remaining = (
        f" Skipped: {open_p1} open P1 {noun} remain from the last reviewed " "round."
        if open_p1 > 0
        else ""
    )
    return (
        f"> 🔁 **Converged** — {format_convergence_stamp(decision=decision)} "
        f"over {decision.stable_rounds} consecutive rounds. No provider call "
        f"was made this round.{remaining} Re-run with `--full` to review again."
    )


def format_run_mechanics(*, metadata: ReviewMetadata) -> str:
    """Format the per-run mechanics footer for a single review run.

    Args:
        metadata: Review run metadata.

    Returns:
        Markdown describing model, provider, tokens, cost, depth, duration,
        and (when instrumented) the per-phase timing breakdown. Estimated
        token/cost figures are prefixed with ``~``.
    """
    estimated = metadata.token_usage_estimated
    total_tokens = int(metadata.token_usage.get("total", 0))
    prompt_tokens = int(metadata.token_usage.get("prompt", 0))
    completion_tokens = int(metadata.token_usage.get("completion", 0))
    source = "estimated" if estimated else "provider-reported"
    parts = [
        "**Model:** "
        + format_sourced_value(
            f"`{sanitize_comment_text(metadata.model, limit=60)}`",
            metadata.model_source or None,
        ),
        "**Provider:** "
        + format_sourced_value(
            f"`{sanitize_comment_text(metadata.provider, limit=40)}`",
            metadata.provider_source or None,
        ),
    ]
    if metadata.transport or metadata.transport_source:
        parts.append(
            "**Transport:** "
            + format_sourced_value(
                f"`{sanitize_comment_text(metadata.transport or 'unset', limit=40)}`",
                metadata.transport_source or None,
            ),
        )
    if metadata.max_cost_usd is not None or metadata.max_cost_usd_source:
        parts.append(
            f"**{MAX_COST_LABEL}:** "
            + format_max_cost_label(
                max_cost_usd=metadata.max_cost_usd,
                source=metadata.max_cost_usd_source or None,
            ),
        )
    parts.extend(
        [
            f"**Depth:** {metadata.depth}",
            (
                f"**Tokens:** {_fmt_tokens(total_tokens, estimated=estimated)} "
                f"(in {_fmt_int(prompt_tokens)} / out {_fmt_int(completion_tokens)}, "
                f"{source})"
            ),
            f"**Est. cost:** "
            f"{_fmt_cost(metadata.cost_estimate_usd, estimated=estimated)}",
            f"**Duration:** {metadata.duration_seconds:.1f}s",
        ],
    )
    timing_summary = sanitized_timing_summary(metadata=metadata)
    if timing_summary:
        # Per-phase breakdown for the run (#2148) on the error-sticky footer.
        parts.append(f"**Timings:** {timing_summary}")
    return " · ".join(parts)


def _severity_counts(*, findings: tuple[ReviewFinding, ...]) -> dict[Severity, int]:
    """Count findings by severity.

    Questions (#1925) carry no severity semantics and are excluded, so the
    counts always match the finding set the derived verdict was computed from.

    Args:
        findings: Findings to count over.

    Returns:
        Count per severity, with every severity present.
    """
    counts: dict[Severity, int] = {Severity.P1: 0, Severity.P2: 0, Severity.P3: 0}
    for finding in findings:
        if finding.is_question:
            continue
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def format_finding_comment(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    inline_fix: InlineFixPlan | None = None,
    title_suffix: str = "",
) -> str:
    """Format a review finding as a GitHub markdown comment (#1911).

    Top to bottom: a severity/category/confidence chip header, a bold title,
    the reasoning **fully visible** (no collapsible — a reviewer should not
    have to click to learn why something is flagged), then one conditional fix
    slot, then the per-finding agent prompt. There is no footer.

    The fix slot follows ``inline_fix``: mode A renders a committable
    ``suggestion`` block, mode B a highlighted ``**Fix:**`` one-liner. Mode A
    keeps the prompt panel as well — the suggestion serves click-to-commit
    reviewers and the prompt serves local-editor and agent users, and both
    describe the identical change.

    Args:
        finding: Review finding to format.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        inline_fix: Fix slot chosen for this finding's inline comment. ``None``
            means the body is being embedded in another surface (the sticky
            comment's folded detail, for instance) rather than posted as an
            inline review comment: a ``suggestion`` block is not committable
            there and a per-finding prompt panel would repeat the fix-all
            prompt already on that surface, so neither is rendered.
        title_suffix: Text appended to the bold title, inside the bold run. A
            regression is re-raised on a *fresh* thread, so without ``"
            (regressed)"`` on the title it reads as a brand-new finding to
            anyone who does not read the provenance blockquote above it.

    Returns:
        Markdown comment body.
    """
    prompt_questions = question_map or {}
    title = sanitize_comment_text(finding.title, limit=200) + title_suffix
    description = sanitize_comment_text(finding.description, limit=2000)
    cause = sanitize_comment_text(finding.cause, limit=2000)

    header = (
        f"{_severity_badge(severity=finding.severity)} · "
        f"{_chip(finding.category)} · {_chip(f'{finding.confidence} confidence')}"
    )
    if finding.source:
        source = sanitize_comment_text(finding.source, limit=100)
        header += f" · {_chip(f'agent: {source}')}"
    lines = [header, "", f"**{title}**"]
    if description.strip():
        lines.extend(["", description])
    if cause.strip():
        lines.extend(["", f"**Root cause:** {cause}"])

    lines.extend(_fix_slot(finding=finding, inline_fix=inline_fix))
    lines.extend(_prompt_slot(finding=finding, inline_fix=inline_fix))

    body = "\n".join(lines)
    if checklist_display in {ChecklistDisplay.LINKED, ChecklistDisplay.ALL}:
        linked = questions_for_finding(
            finding=finding,
            question_map=prompt_questions,
        )
        body += format_review_questions_markdown(questions=linked)
    return body


def _fix_slot(
    *,
    finding: ReviewFinding,
    inline_fix: InlineFixPlan | None,
) -> list[str]:
    """Render the conditional fix slot for a finding comment.

    Args:
        finding: Finding being rendered.
        inline_fix: Chosen fix plan, or ``None`` for a non-inline surface.

    Returns:
        Markdown lines for the slot; empty when the finding names no fix at
        all.
    """
    change = inline_fix.committable_change if inline_fix is not None else None
    if change is not None:
        return ["", _suggestion_block(replacement=change.replacement)]
    fix = sanitize_comment_text(finding.fix, limit=2000).strip()
    if not fix:
        return []
    # Mode B has no committable block to draw the eye, so the described fix is
    # highlighted instead of sitting as one more bold run in the prose. A
    # ``[!TIP]`` alert is a plain blockquote to GitHub, so it neither nests
    # inside nor collides with the ``[!IMPORTANT]`` prompt panel that follows.
    quoted = [f"> {line}".rstrip() for line in f"**Fix:** {fix}".splitlines()]
    return ["", "> [!TIP]", *quoted]


def _prompt_slot(
    *,
    finding: ReviewFinding,
    inline_fix: InlineFixPlan | None,
) -> list[str]:
    """Render the per-finding agent prompt panel, when it earns its space.

    The panel is gated to P1 and P2 (#1911): a nit does not warrant a
    copy-paste agent hand-off, and a panel on every P3 turns the affordance
    into wallpaper. Questions carry no fix and are excluded by the prompt
    renderer itself.

    Args:
        finding: Finding being rendered.
        inline_fix: Chosen fix plan, or ``None`` for a non-inline surface.

    Returns:
        Markdown lines for the panel, or an empty list when it is gated off.
    """
    if inline_fix is None or finding.severity not in _PROMPT_SEVERITIES:
        return []
    panel = render_finding_prompt_panel(
        finding=finding,
        # In mode A the prompt must land the same edit the suggestion would, or
        # the two paths silently diverge and whichever the reader picks is a
        # coin flip.
        suggested_change=inline_fix.committable_change,
    )
    return ["", panel] if panel else []


def _suggestion_block(*, replacement: str) -> str:
    """Render a GitHub ``suggestion`` block around untrusted replacement text.

    Args:
        replacement: Full replacement for the anchored lines.

    Returns:
        The fenced ``suggestion`` block.
    """
    # Neutralize fence break-out and @mentions in untrusted model code. The
    # suggestion body renders as Markdown, so an unescaped `@user` still pings.
    safe = replacement.replace("```", "``​`")
    safe = _MENTION_RE.sub("@​", safe)
    return "```suggestion\n" + safe + "\n```"


def _is_diff_mappable(
    *,
    finding: ReviewFinding,
    diff_lines: dict[str, set[int]] | None,
) -> bool:
    """Report whether a finding maps onto a line inside the PR diff.

    A diff-mappable finding also posts as an inline review comment, so the
    sticky comment is not its only surface. A non-diff-mappable ("fallback")
    finding has no inline path and must survive sticky-comment truncation.

    Args:
        finding: Review finding to classify.
        diff_lines: Map of repo-relative path to the set of diff-covered line
            numbers, or ``None`` when the diff is unavailable (all findings are
            then treated as fallback).

    Returns:
        True when the finding lands on a diff-covered line, else False.
    """
    rel = normalize_diff_path(finding.file)
    if not rel or finding.line <= 0 or diff_lines is None:
        return False
    return finding.line in diff_lines.get(rel, set())


def _location_label(*, finding: ReviewFinding) -> str:
    """Format a ``file:line`` code label for a finding, or empty when unknown."""
    if not finding.file:
        return ""
    safe = sanitize_comment_text(finding.file, limit=200)
    if finding.line > 0:
        return f"`{safe}:{finding.line}`"
    return f"`{safe}`"


def _format_checklist_appendix_markdown(*, result: ReviewResult) -> list[str]:
    """Build cleared/orphan checklist appendix lines for markdown."""
    cleared = cleared_answers(answers=result.checklist)
    orphans = orphan_concerns(
        answers=result.checklist,
        findings=result.findings,
    )
    lines = ["", f"### Cleared checks ({len(cleared)})"]
    if cleared:
        for answer in cleared:
            question = sanitize_comment_text(
                answer.question or f"(checklist item {answer.id})",
                limit=300,
            )
            lines.append(f"- ✓ {question}")
    else:
        lines.append("- (none)")

    lines.extend(["", f"### Checklist concerns without findings ({len(orphans)})"])
    if orphans:
        for answer in orphans:
            question = sanitize_comment_text(
                answer.question or f"(checklist item {answer.id})",
                limit=300,
            )
            evidence = sanitize_comment_text(answer.evidence, limit=200).replace(
                "|",
                "\\|",
            )
            lines.append(f"- {question}")
            if evidence.strip():
                lines.append(f"  - {evidence}")
    else:
        lines.append("- (none — good)")
    return lines


def _partition_findings(
    *,
    findings: tuple[ReviewFinding, ...],
    diff_lines: dict[str, set[int]] | None,
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    """Split findings into inline-capable and fallback groups.

    Args:
        findings: Findings to partition.
        diff_lines: Diff line map; ``None`` classifies every finding as fallback.

    Returns:
        Tuple of ``(inline, fallback)`` finding lists.
    """
    inline: list[ReviewFinding] = []
    fallback: list[ReviewFinding] = []

    for finding in findings:
        if _is_diff_mappable(finding=finding, diff_lines=diff_lines):
            inline.append(finding)
        else:
            fallback.append(finding)

    return inline, fallback
