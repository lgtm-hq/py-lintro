"""The one body-assembly pipeline for GitHub AI-review comments (#2304).

Three surfaces post Markdown on a pull request — the sticky mission-control
board, the per-round review body, and the failure comment — and each used to
join its own list of strings with its own separator and its own size cap. The
shapes differed; the assembly did not. :class:`Section` and :func:`assemble`
are that assembly, and all three paths go through them, so a change to how a
comment is put together is one edit rather than three.

Sizing is not re-implemented here: :func:`assemble` caps through
``github_contract.cap_body``, and the sticky renderer — the only surface with
prunable sections — passes ``budget=None`` because ``contract.fit_body`` owns
the cap for that path and has to measure un-capped candidates to prune them.

The module also renders the inline finding comment, the one review surface
that is a *whole comment per finding* rather than an assembled body.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lintro.ai.review.agent_prompts import render_finding_prompt_panel
from lintro.ai.review.checklist_display import (
    cleared_answers,
    format_review_questions_markdown,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github_badges import (
    format_badge_table,
    format_badge_tables,
    run_stats_primary_cells,
)
from lintro.ai.review.github_constants import _MENTION_RE, _SEVERITY_EMOJI
from lintro.ai.review.github_contract import (
    DEFAULT_BUDGET,
    CommentBudget,
    cap_body,
)
from lintro.ai.review.github_notes import (
    format_convergence_banner,
    format_convergence_note,
    format_coverage_limited_warning,
    format_cross_chunk_note,
    format_inline_post_cause,
    format_inline_post_note,
    format_run_mechanics,
    format_synthesis_note_line,
    format_timings_note,
    sanitized_timing_summary,
)
from lintro.ai.review.inline_fix import InlineFixPlan, normalize_diff_path
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "REGRESSED_TITLE_SUFFIX",
    "SECTION_SEPARATOR",
    "Section",
    "assemble",
    "format_badge_table",
    "format_badge_tables",
    "format_convergence_banner",
    "format_convergence_note",
    "format_coverage_limited_warning",
    "format_cross_chunk_note",
    "format_finding_comment",
    "format_inline_post_cause",
    "format_inline_post_note",
    "format_run_mechanics",
    "format_synthesis_note_line",
    "format_timings_note",
    "run_stats_primary_cells",
    "sanitize_comment_text",
    "sanitized_timing_summary",
]

#: Blank line between two rendered sections. Every GitHub comment surface used
#: this separator before the pipeline existed, so pinning it here is what makes
#: the convergence byte-identical rather than merely equivalent.
SECTION_SEPARATOR = "\n\n"


@dataclass(frozen=True, kw_only=True, slots=True)
class Section:
    """One named block of Markdown in an assembled comment body.

    The name is not rendered. It exists so a body reads as an ordered list of
    parts a reviewer can point at — "the coverage row", "the run-history
    fold" — instead of an anonymous list of strings, and so a test can assert
    which parts a surface produced without matching on prose.

    Attributes:
        name: Stable identifier for this block, for tests and debugging.
        text: Rendered Markdown. Empty text is omitted from the body, which is
            how every optional section opts out.
    """

    name: str
    text: str


def assemble(
    *,
    sections: Sequence[Section],
    budget: CommentBudget | None = DEFAULT_BUDGET,
) -> str:
    """Join a comment's sections into the body that gets posted.

    Empty sections are dropped rather than rendered as blank space, so an
    optional block opts out by returning ``""``.

    Args:
        sections: Ordered sections, top of the comment first.
        budget: Budget the finished body is capped to. ``None`` skips the cap
            for a caller that owns sizing itself — in practice the sticky
            renderer, whose ``fit_body`` search has to see un-capped
            candidates to know which sections to prune.

    Returns:
        str: The assembled Markdown body.
    """
    body = SECTION_SEPARATOR.join(section.text for section in sections if section.text)
    if budget is None:
        return body
    return cap_body(body=body, budget=budget)


REGRESSED_TITLE_SUFFIX = " (regressed)"

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
