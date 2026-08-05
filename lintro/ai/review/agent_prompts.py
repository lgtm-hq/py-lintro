"""Copyable AI-agent remediation prompts for GitHub review surfaces.

Pure re-rendering of already-parsed :class:`ReviewFinding` data into a prompt a
developer can paste into a coding agent. No model call is made here.

Three call sites consume this module (epic #1905):

* the sticky status comment (#1909) renders a fix-all prompt scoped to *all*
  still-open findings across rounds;
* the per-review comment body (#1910) renders a fix-all prompt scoped to *this
  round's* findings only;
* inline review comments (#1911) render a single-finding prompt.

Because two fix-all prompts can be visible on the same PR at once, the scope is
restated on the prompt's own first line as well as in the panel title, so a
copied prompt is never ambiguous about which findings it covers.
"""

from __future__ import annotations

import re
import textwrap

from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.suggested_change import SuggestedChange
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "SPECULATIVE_NOTICE",
    "VERIFICATION_PREAMBLE",
    "prompt_findings",
    "render_agent_prompt",
    "render_agent_prompt_panel",
    "render_finding_prompt",
    "render_finding_prompt_panel",
    "render_prompt_panel",
    "render_sticky_prompt_pointer",
]

#: Verbatim verification instruction that opens every generated prompt. Agents
#: must re-check each finding against current code before changing anything.
VERIFICATION_PREAMBLE = (
    "These are open findings from a lintro AI code review. Verify each one "
    "against the current code. Fix only still-valid issues, skip the rest with "
    "a brief reason, keep changes minimal, and validate with tests."
)

#: Verbatim caution appended to a speculative finding's prompt block. The model
#: self-reports the evidence basis (#1925); an inferred finding must be
#: reproduced before an agent starts editing code on its say-so.
SPECULATIVE_NOTICE = (
    "This finding is inferred, not verified — confirm it reproduces before fixing."
)

#: Column at which prompt prose is soft-wrapped inside the fenced code block.
_WRAP_WIDTH = 80

#: Indent applied to continuation lines under a finding bullet.
_CONTINUATION_INDENT = "  "

_BACKTICK_RUN_RE = re.compile(r"`+")

_TITLE_LIMIT = 200
_TEXT_LIMIT = 2000
_PATH_LIMIT = 300

_FOOTERS: dict[AgentPromptScopeKind, str] = {
    AgentPromptScopeKind.ALL_OPEN: (
        "Regenerated every run · covers exactly the open findings above"
    ),
    AgentPromptScopeKind.THIS_REVIEW: (
        "For everything still open across all rounds, use the sticky comment's "
        "fix-all prompt"
    ),
    AgentPromptScopeKind.SINGLE_FINDING: (
        "Paste into Claude Code, Cursor, or any coding agent"
    ),
}

_MISSING_FOOTERS = set(AgentPromptScopeKind) - set(_FOOTERS)
if _MISSING_FOOTERS:  # pragma: no cover - guards a future scope kind
    raise RuntimeError(
        f"AgentPromptScopeKind members without a default footer: {_MISSING_FOOTERS}",
    )


def _plural(*, count: int, noun: str) -> str:
    """Format a count with a naively pluralized noun.

    Args:
        count: Number of items.
        noun: Singular noun to pluralize with a trailing ``s``.

    Returns:
        The count and noun, e.g. ``"1 finding"`` or ``"3 findings"``.
    """
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _wrap(*, text: str, initial_indent: str = "", subsequent_indent: str = "") -> str:
    """Soft-wrap prose to the prompt width without breaking words.

    Args:
        text: Prose to wrap. Internal whitespace is collapsed.
        initial_indent: Indent for the first output line.
        subsequent_indent: Indent for every following line.

    Returns:
        The wrapped text, or an empty string when ``text`` has no content.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    return textwrap.fill(
        collapsed,
        width=_WRAP_WIDTH,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _fence_for(*, text: str) -> str:
    """Build a code fence longer than any backtick run inside ``text``.

    A finding's reasoning can legitimately contain triple backticks; a fixed
    ```` ``` ```` fence would then be closed early and the rest of the prompt
    would escape the code block.

    Args:
        text: Prompt body that will be placed inside the fence.

    Returns:
        A run of at least three backticks, always longer than the longest run
        found in ``text``.
    """
    longest = max((len(run) for run in _BACKTICK_RUN_RE.findall(text)), default=0)
    return "`" * max(3, longest + 1)


def _scope_sentence(*, scope: AgentPromptScope, count: int) -> str:
    """Build the scope sentence restated on the prompt's first line.

    Args:
        scope: Scope descriptor for the prompt.
        count: Number of findings the prompt covers.

    Returns:
        A single sentence naming exactly which findings are in scope.

    Raises:
        ValueError: When ``scope.kind`` is not a handled member of
            :class:`AgentPromptScopeKind`.
    """
    if scope.kind is AgentPromptScopeKind.SINGLE_FINDING:
        return "Scope: this single finding from a lintro AI code review."
    if scope.kind is AgentPromptScopeKind.ALL_OPEN:
        quantifier = "the 1 finding" if count == 1 else f"ALL {count} findings"
        after = (
            f" after round {scope.round_number}"
            if scope.round_number is not None and scope.round_number > 1
            else ""
        )
        return (
            f"Scope: {quantifier} still open on this PR{after} "
            "(not just the latest review)."
        )
    if scope.kind is AgentPromptScopeKind.THIS_REVIEW:
        where = (
            f"round {scope.round_number}"
            if scope.round_number is not None
            else "the latest round"
        )
        return (
            f"Scope: the {_plural(count=count, noun='finding')} posted in {where} of "
            "this PR's lintro review ONLY (older open findings are covered by the "
            "fix-all prompt on the sticky status comment)."
        )
    # Exhaustiveness guard twin to _FOOTERS' _MISSING_FOOTERS check: a new
    # AgentPromptScopeKind added without a branch here must fail loudly
    # instead of silently falling through to THIS_REVIEW-flavored text.
    raise ValueError(f"Unhandled AgentPromptScopeKind: {scope.kind!r}")


def _panel_title(*, scope: AgentPromptScope, count: int) -> str:
    """Build the visible panel header title for a prompt.

    Args:
        scope: Scope descriptor for the prompt.
        count: Number of findings the prompt covers.

    Returns:
        Panel title text, without the leading ``⚡``.

    Raises:
        ValueError: When ``scope.kind`` is not a handled member of
            :class:`AgentPromptScopeKind`.
    """
    if scope.kind is AgentPromptScopeKind.SINGLE_FINDING:
        return "Prompt for AI agents"
    if scope.kind is AgentPromptScopeKind.ALL_OPEN:
        rounds = ""
        if scope.round_number is not None:
            rounds = (
                " (round 1)"
                if scope.round_number <= 1
                else f" (rounds 1–{scope.round_number})"
            )
        noun = _plural(count=count, noun="still-open finding")
        quantifier = noun if count == 1 else f"all {noun}"
        return f"Fix-all prompt — {quantifier}{rounds}"
    if scope.kind is AgentPromptScopeKind.THIS_REVIEW:
        return f"Fix prompt — this round's {_plural(count=count, noun='finding')} only"
    # Exhaustiveness guard twin to _FOOTERS' _MISSING_FOOTERS check: a new
    # AgentPromptScopeKind added without a branch here must fail loudly
    # instead of silently falling through to THIS_REVIEW-flavored text.
    raise ValueError(f"Unhandled AgentPromptScopeKind: {scope.kind!r}")


def _group_by_file(
    *,
    findings: tuple[ReviewFinding, ...],
) -> list[tuple[str, list[ReviewFinding]]]:
    """Group findings by file, preserving caller order.

    Files appear in first-seen order and findings keep their incoming order
    within each file, so an already severity-sorted list stays sorted.

    Args:
        findings: Findings to group.

    Returns:
        Pairs of file path and the findings recorded against it.
    """
    grouped: dict[str, list[ReviewFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.file, []).append(finding)
    return list(grouped.items())


def _occurrence_lines(*, finding: ReviewFinding) -> list[str]:
    """Render every location of a repeated finding pattern.

    Display collapses a repeated pattern to one thread, but the prompt must
    not: an agent handed "fix this" for a pattern with twenty call sites has
    to be told about all twenty, or nineteen silently survive the fix.

    Args:
        finding: Finding whose occurrences are being enumerated.

    Returns:
        Prompt lines listing every occurrence, or an empty list when the
        pattern occurs only once.
    """
    occurrences = finding.all_occurrences
    if len(occurrences) < 2:
        return []
    lines = [
        _wrap(
            text=(
                f"Occurs at {len(occurrences)} locations — apply the "
                "equivalent fix at each and verify each one still reproduces "
                "before changing it:"
            ),
            initial_indent=_CONTINUATION_INDENT,
            subsequent_indent=_CONTINUATION_INDENT,
        ),
    ]
    lines.extend(
        _wrap(
            text=(
                f"- {sanitize_comment_text(occurrence.file, limit=_PATH_LIMIT)}"
                f":{occurrence.line}"
            ),
            initial_indent=_CONTINUATION_INDENT * 2,
            subsequent_indent=_CONTINUATION_INDENT * 3,
        )
        for occurrence in occurrences
    )
    return lines


def prompt_findings(
    *,
    findings: tuple[ReviewFinding, ...],
) -> tuple[ReviewFinding, ...]:
    """Select the entries a remediation prompt should cover.

    Questions (#1925) are excluded from every prompt scope: there is nothing
    to fix until the author answers, and a question promoted to a real finding
    next round arrives with its own severity and prompt.

    Args:
        findings: Candidate entries in presentation order.

    Returns:
        The subset that represents actionable findings.
    """
    return tuple(finding for finding in findings if not finding.is_question)


def _finding_block(*, finding: ReviewFinding) -> list[str]:
    """Render one finding as a bullet plus indented continuation lines.

    Args:
        finding: Finding to render.

    Returns:
        Prompt lines for the finding.
    """
    title = sanitize_comment_text(finding.title, limit=_TITLE_LIMIT)
    category = sanitize_comment_text(finding.category, limit=60)
    bullet = (
        f"- Line {finding.line} — **{title}** "
        f"({finding.severity.value} · {category}):"
    )
    lines = [
        _wrap(text=bullet, subsequent_indent=_CONTINUATION_INDENT),
    ]

    reasoning = " ".join(
        part
        for part in (
            sanitize_comment_text(finding.description, limit=_TEXT_LIMIT).strip(),
            sanitize_comment_text(finding.cause, limit=_TEXT_LIMIT).strip(),
        )
        if part
    )
    if reasoning:
        lines.append(
            _wrap(
                text=reasoning,
                initial_indent=_CONTINUATION_INDENT,
                subsequent_indent=_CONTINUATION_INDENT,
            ),
        )
    fix = sanitize_comment_text(finding.fix, limit=_TEXT_LIMIT).strip()
    if fix:
        lines.append(
            _wrap(
                text=f"Fix: {fix}",
                initial_indent=_CONTINUATION_INDENT,
                subsequent_indent=_CONTINUATION_INDENT,
            ),
        )
    if finding.evidence_style is EvidenceStyle.SPECULATIVE:
        lines.append(
            _wrap(
                text=SPECULATIVE_NOTICE,
                initial_indent=_CONTINUATION_INDENT,
                subsequent_indent=_CONTINUATION_INDENT,
            ),
        )
    lines.extend(_occurrence_lines(finding=finding))
    return lines


def render_agent_prompt(
    *,
    findings: tuple[ReviewFinding, ...],
    scope: AgentPromptScope,
) -> str:
    """Render the copyable agent prompt body for a set of findings.

    The prompt opens with a scope sentence (so a copied prompt can never be
    confused with the other surface's prompt), then the verbatim verification
    preamble, then the findings grouped by file. Questions are dropped before
    anything is counted or rendered, so the scope sentence never promises a
    fix for something that only asked a question.

    Args:
        findings: Findings in scope, in the order they should be presented.
        scope: Which finding set the prompt covers.

    Returns:
        Plain-text prompt body, or an empty string when the scope holds no
        actionable findings (questions do not count).
    """
    actionable = prompt_findings(findings=findings)
    if not actionable:
        return ""

    sections: list[str] = [
        _wrap(text=_scope_sentence(scope=scope, count=len(actionable))),
        _wrap(text=VERIFICATION_PREAMBLE),
    ]
    for path, file_findings in _group_by_file(findings=actionable):
        safe_path = sanitize_comment_text(path, limit=_PATH_LIMIT)
        sections.append(f"In `{safe_path}`:")
        sections.extend(
            "\n".join(_finding_block(finding=finding)) for finding in file_findings
        )
    return "\n\n".join(sections)


def _suggested_change_section(*, change: SuggestedChange) -> str:
    """Render the block tying a prompt to this comment's suggestion block.

    An inline comment in mode A (#1911) carries both a committable suggestion
    and this prompt. They must specify the *same* edit: a reviewer who commits
    the suggestion and an agent handed the prompt have to end up with identical
    code, so the prompt restates the replacement verbatim instead of
    paraphrasing it.

    Args:
        change: The change rendered as the comment's suggestion block.

    Returns:
        Prompt lines naming the range, then the replacement in its own fenced
        block, byte-for-byte as the suggestion renders it.
    """
    span = (
        f"line {change.start_line}"
        if not change.is_multiline
        else f"lines {change.start_line}-{change.end_line}"
    )
    header = _wrap(
        text=(
            "Apply exactly the change already proposed in this comment's "
            f"suggestion block — replace {span} with the following, verbatim:"
        ),
    )
    # Rendered raw: no continuation indent and no per-line truncation. Both
    # would silently corrupt an indentation-sensitive replacement, and the
    # suggestion block applies neither — the two paths have to produce
    # identical code. Only mention-neutralization is applied, matching
    # ``_suggestion_block``; ``plan_inline_fix`` already bounds the total size.
    # The fence is sized against the text so a replacement containing its own
    # backticks cannot close it early.
    body = "\n".join(
        sanitize_comment_text(line) for line in change.replacement.splitlines() or [""]
    )
    fence = _fence_for(text=body)
    return f"{header}\n{fence}\n{body}\n{fence}"


def render_finding_prompt(
    *,
    finding: ReviewFinding,
    suggested_change: SuggestedChange | None = None,
) -> str:
    """Render the single-finding agent prompt used by inline comments.

    Args:
        finding: Finding the inline comment is anchored to.
        suggested_change: The change the same comment renders as a committable
            suggestion block, when it has one. Restated verbatim so the prompt
            and the suggestion cannot drift apart.

    Returns:
        Plain-text prompt body scoped to exactly this finding, or an empty
        string when the entry is a question — questions get no prompt panel.
    """
    prompt = render_agent_prompt(
        findings=(finding,),
        scope=AgentPromptScope(kind=AgentPromptScopeKind.SINGLE_FINDING),
    )
    if not prompt or suggested_change is None:
        return prompt
    return f"{prompt}\n\n{_suggested_change_section(change=suggested_change)}"


def render_prompt_panel(*, prompt: str, title: str, footer: str = "") -> str:
    """Render a prompt inside the shared purple alert panel.

    The panel is a GitHub ``[!IMPORTANT]`` alert whose header stays visible and
    whose body is collapsed behind ``<details>``. The prompt itself sits in a
    fenced code block so GitHub renders a native copy button.

    Args:
        prompt: Prompt body to embed.
        title: Header title shown next to the ``⚡``.
        footer: Optional small-print line under the collapsed body.

    Returns:
        Markdown for the panel, or an empty string when ``prompt`` is blank.
    """
    if not prompt.strip():
        return ""
    fence = _fence_for(text=prompt)
    lines = [
        "> [!IMPORTANT]",
        f"> ⚡ **{title}**",
        ">",
        "> <details><summary>Show prompt</summary>",
        ">",
        f"> {fence}",
        *(f"> {line}".rstrip() for line in prompt.splitlines()),
        f"> {fence}",
        ">",
        "> </details>",
    ]
    if footer:
        lines.extend([">", f"> <sub>{footer}</sub>"])
    return "\n".join(lines)


def render_agent_prompt_panel(
    *,
    findings: tuple[ReviewFinding, ...],
    scope: AgentPromptScope,
    footer: str | None = None,
) -> str:
    """Render the fix-all prompt for a finding set inside the shared panel.

    Args:
        findings: Findings in scope, in presentation order.
        scope: Which finding set the prompt covers.
        footer: Small-print line under the collapsed body. Defaults to the
            scope's standard footer; pass ``""`` to omit it.

    Returns:
        Markdown for the panel, or an empty string when there are no findings.
    """
    prompt = render_agent_prompt(findings=findings, scope=scope)
    if not prompt:
        return ""
    return render_prompt_panel(
        prompt=prompt,
        title=_panel_title(scope=scope, count=len(prompt_findings(findings=findings))),
        footer=_FOOTERS[scope.kind] if footer is None else footer,
    )


def render_finding_prompt_panel(
    *,
    finding: ReviewFinding,
    suggested_change: SuggestedChange | None = None,
    footer: str | None = None,
) -> str:
    """Render the single-finding prompt panel used by inline comments.

    Args:
        finding: Finding the inline comment is anchored to.
        suggested_change: The change the same comment renders as a committable
            suggestion block, when it has one (#1911). The prompt then restates
            that exact replacement so both paths apply the identical fix.
        footer: Small-print line under the collapsed body. Defaults to the
            single-finding footer; pass ``""`` to omit it.

    Returns:
        Markdown for the panel, or an empty string when the entry is a
        question.
    """
    scope = AgentPromptScope(kind=AgentPromptScopeKind.SINGLE_FINDING)
    prompt = render_finding_prompt(
        finding=finding,
        suggested_change=suggested_change,
    )
    if not prompt:
        return ""
    return render_prompt_panel(
        prompt=prompt,
        title=_panel_title(scope=scope, count=1),
        footer=_FOOTERS[scope.kind] if footer is None else footer,
    )


def render_sticky_prompt_pointer(*, sticky_url: str = "") -> str:
    """Render the one-line pointer that replaces a duplicate fix-all panel.

    When this round's findings are exactly the PR's still-open findings, the
    per-review body would repeat the sticky comment's prompt verbatim. It links
    to the sticky instead, so there is never a wrong prompt to copy.

    Args:
        sticky_url: URL of the sticky status comment. When empty (the sticky's
            id is not known yet, e.g. the comment is being created in this same
            run) the pointer renders unlinked rather than as a dead link.

    Returns:
        Markdown for the pointer line.
    """
    target = (
        f"[sticky comment's fix-all]({sticky_url})"
        if sticky_url
        else "sticky comment's fix-all"
    )
    return f"⚡ Fix prompt: identical to the {target} this round — copy it there."
