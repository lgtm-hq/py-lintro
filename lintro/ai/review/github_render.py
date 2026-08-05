"""Finding and summary rendering for GitHub AI-review comments."""

from __future__ import annotations

from lintro.ai.review.agent_prompts import render_finding_prompt_panel
from lintro.ai.review.checklist_display import (
    cleared_answers,
    format_review_questions_markdown,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github_constants import _MENTION_RE, _SEVERITY_EMOJI
from lintro.ai.review.inline_fix import InlineFixPlan, normalize_diff_path
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "format_finding_comment",
    "format_review_summary",
    "format_run_mechanics",
    "sanitize_comment_text",
]

#: Severities that earn a per-finding agent prompt panel (#1911). A P3 nit gets
#: none: the panel is an affordance, and one on every finding is wallpaper.
_PROMPT_SEVERITIES: frozenset[Severity] = frozenset({Severity.P1, Severity.P2})


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


def format_run_mechanics(*, metadata: ReviewMetadata) -> str:
    """Format the per-run mechanics footer for a single review run.

    Args:
        metadata: Review run metadata.

    Returns:
        Markdown describing model, provider, tokens, cost, depth, and duration.
        Estimated token/cost figures are prefixed with ``~``.
    """
    estimated = metadata.token_usage_estimated
    total_tokens = int(metadata.token_usage.get("total", 0))
    prompt_tokens = int(metadata.token_usage.get("prompt", 0))
    completion_tokens = int(metadata.token_usage.get("completion", 0))
    source = "estimated" if estimated else "provider-reported"
    parts = [
        f"**Model:** `{sanitize_comment_text(metadata.model, limit=60)}`",
        f"**Provider:** `{sanitize_comment_text(metadata.provider, limit=40)}`",
        f"**Depth:** {metadata.depth}",
        (
            f"**Tokens:** {_fmt_tokens(total_tokens, estimated=estimated)} "
            f"(in {_fmt_int(prompt_tokens)} / out {_fmt_int(completion_tokens)}, "
            f"{source})"
        ),
        f"**Est. cost:** {_fmt_cost(metadata.cost_estimate_usd, estimated=estimated)}",
        f"**Duration:** {metadata.duration_seconds:.1f}s",
    ]
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


def _count_row(*, counts: dict[Severity, int]) -> list[str]:
    """Render the severity count table."""
    return [
        "| 🔴 P1 | 🟠 P2 | 🟡 P3 |",
        "|:-:|:-:|:-:|",
        (
            f"| **{counts[Severity.P1]}** | **{counts[Severity.P2]}** | "
            f"**{counts[Severity.P3]}** |"
        ),
    ]


def format_finding_comment(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    inline_fix: InlineFixPlan | None = None,
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

    Returns:
        Markdown comment body.
    """
    prompt_questions = question_map or {}
    title = sanitize_comment_text(finding.title, limit=200)
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
    return ["", f"**Fix:** {fix}"]


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


def format_review_summary(
    *,
    result: ReviewResult,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    diff_lines: dict[str, set[int]] | None = None,
    findings_char_budget: int | None = None,
) -> str:
    """Format the per-run review summary section.

    Produces the scannable body for a single run: header line, partial-state
    note, severity count table, TL;DR, a compact findings list with collapsible
    detail, and (optionally) the checklist appendix.

    Args:
        result: Review result to summarize.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for the checklist appendix.
        diff_lines: Diff line map used to order fallback findings first in the
            findings section. ``None`` treats all findings as fallback.
        findings_char_budget: Optional soft character budget for the findings
            section; overflow is replaced by an explicit truncation marker.

    Returns:
        Markdown summary section body.
    """
    metadata = result.metadata
    counts = _severity_counts(findings=result.findings)
    est = metadata.token_usage_estimated
    head_bits = [
        f"**{metadata.files_reviewed} files** reviewed",
        f"depth {metadata.depth}",
        f"`{sanitize_comment_text(metadata.model, limit=60)}`",
        _fmt_cost(metadata.cost_estimate_usd, estimated=est),
    ]
    lines = [
        "## 🔎 Lintro Review",
        "",
        "> " + " · ".join(head_bits),
    ]
    if metadata.partial:
        reason = sanitize_comment_text(
            metadata.stopped_reason or "incomplete",
            limit=60,
        )
        if metadata.chunks_reviewed <= 0:
            note = (
                f"> ⚠️ **Partial review** — stopped at {reason} before "
                f"reviewing any of {metadata.chunks_total} chunks. No findings "
                "were produced. Raise `ai.max_cost_usd` or narrow `--path`, then "
                "re-run."
            )
        else:
            note = (
                f"> ⚠️ **Partial review** — stopped at {reason} after "
                f"{metadata.chunks_reviewed} of {metadata.chunks_total} "
                "chunks. Findings below cover only the reviewed portion. Raise "
                "`ai.max_cost_usd` or narrow `--path` to review the rest."
            )
        lines.extend(["", note])

    lines.extend(["", *_count_row(counts=counts)])

    summary_text = sanitize_comment_text(result.summary or "(no summary)", limit=4000)
    lines.extend(["", f"**TL;DR** — {summary_text}"])

    lines.extend(
        _format_findings_section(
            findings=result.findings,
            checklist_display=checklist_display,
            question_map=question_map or {},
            diff_lines=diff_lines,
            char_budget=findings_char_budget,
        ),
    )

    lines.append(f"\n**Structured checks:** {metadata.checklist_items}")

    if checklist_display == ChecklistDisplay.ALL:
        lines.extend(_format_checklist_appendix_markdown(result=result))

    return "\n".join(lines)


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


def _finding_block(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
) -> list[str]:
    """Render the markdown lines for a single finding in the findings list."""
    location = _location_label(finding=finding)
    title = sanitize_comment_text(finding.title, limit=200)
    headline = (
        f"{_severity_badge(severity=finding.severity)} · "
        f"{_chip(finding.category)} — **{title}**"
    )
    if location:
        headline += f" · {location}"
    body = format_finding_comment(
        finding=finding,
        checklist_display=checklist_display,
        question_map=question_map,
    )
    return [
        "",
        headline,
        "",
        "<details><summary>Details</summary>",
        "",
        body,
        "",
        "</details>",
    ]


def _format_findings_section(
    *,
    findings: tuple[ReviewFinding, ...],
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
    diff_lines: dict[str, set[int]] | None = None,
    char_budget: int | None = None,
) -> list[str]:
    """Render a compact, collapsible list of all findings.

    Non-diff-mappable ("fallback") findings render first: they have no inline
    surface, so if truncation must drop anything it only drops findings that
    also exist as inline comments. When ``char_budget`` is set and the rendered
    blocks would overflow it, rendering stops early and an explicit marker names
    how many findings were dropped so nothing silently vanishes.

    Args:
        findings: Findings to render.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        diff_lines: Diff line map used to order fallback findings first. ``None``
            treats every finding as fallback (preserving severity ordering).
        char_budget: Optional soft character budget for the finding blocks. When
            exceeded, remaining *diff-mappable* findings are replaced by a
            truncation marker (they still exist as inline comments). Fallback
            findings are never budget-truncated — they have no other surface.

    Returns:
        Markdown lines for the findings section.
    """
    if not findings:
        return ["", "### Findings", "", "✅ No actionable findings."]

    ordered = sorted(
        findings,
        key=lambda f: (
            _is_diff_mappable(finding=f, diff_lines=diff_lines),
            f.severity.value,
            f.file,
            f.line,
        ),
    )
    lines = ["", f"### Findings ({len(findings)})"]
    used = 0
    for index, finding in enumerate(ordered):
        block = _finding_block(
            finding=finding,
            checklist_display=checklist_display,
            question_map=question_map,
        )
        block_len = len("\n".join(block))
        # The budget is enforced on *every* finding so the section always fits
        # inside the caller's char_budget — otherwise ``_cap_body`` would later
        # trim the sticky from the tail and could silently drop findings.
        # Fallback (non-diff-mappable) findings sort first, so they are only ever
        # dropped when fallback content alone exceeds GitHub's hard comment limit
        # (unavoidable). The marker text adapts: if any dropped finding is a
        # fallback (no inline surface), it points to the workflow logs rather
        # than to inline comments that do not exist for it. ``index > 0`` always
        # renders at least one finding so a single oversized block is not lost.
        if char_budget is not None and index > 0 and used + block_len > char_budget:
            remaining = ordered[index:]
            dropped = len(remaining)
            any_fallback = any(
                not _is_diff_mappable(finding=item, diff_lines=diff_lines)
                for item in remaining
            )
            where = (
                "the workflow logs"
                if any_fallback
                else "the inline comments and workflow logs"
            )
            lines.extend(
                [
                    "",
                    f"> ✂️ **{dropped} more finding(s) truncated** to fit "
                    f"GitHub's size limit — see {where} for the full list.",
                ],
            )
            break
        lines.extend(block)
        used += block_len
    return lines


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
