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
from lintro.ai.review.github_render import sanitize_comment_text
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "VERIFICATION_PREAMBLE",
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


def _panel_title(*, scope: AgentPromptScope, count: int) -> str:
    """Build the visible panel header title for a prompt.

    Args:
        scope: Scope descriptor for the prompt.
        count: Number of findings the prompt covers.

    Returns:
        Panel title text, without the leading ``⚡``.
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
    return f"Fix prompt — this round's {_plural(count=count, noun='finding')} only"


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
    return lines


def render_agent_prompt(
    *,
    findings: tuple[ReviewFinding, ...],
    scope: AgentPromptScope,
) -> str:
    """Render the copyable agent prompt body for a set of findings.

    The prompt opens with a scope sentence (so a copied prompt can never be
    confused with the other surface's prompt), then the verbatim verification
    preamble, then the findings grouped by file.

    Args:
        findings: Findings in scope, in the order they should be presented.
        scope: Which finding set the prompt covers.

    Returns:
        Plain-text prompt body, or an empty string when there are no findings.
    """
    if not findings:
        return ""

    sections: list[str] = [
        _wrap(text=_scope_sentence(scope=scope, count=len(findings))),
        _wrap(text=VERIFICATION_PREAMBLE),
    ]
    for path, file_findings in _group_by_file(findings=findings):
        safe_path = sanitize_comment_text(path, limit=_PATH_LIMIT)
        sections.append(f"In `{safe_path}`:")
        sections.extend(
            "\n".join(_finding_block(finding=finding)) for finding in file_findings
        )
    return "\n\n".join(sections)


def render_finding_prompt(*, finding: ReviewFinding) -> str:
    """Render the single-finding agent prompt used by inline comments.

    Args:
        finding: Finding the inline comment is anchored to.

    Returns:
        Plain-text prompt body scoped to exactly this finding.
    """
    return render_agent_prompt(
        findings=(finding,),
        scope=AgentPromptScope(kind=AgentPromptScopeKind.SINGLE_FINDING),
    )


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
        title=_panel_title(scope=scope, count=len(findings)),
        footer=_FOOTERS[scope.kind] if footer is None else footer,
    )


def render_finding_prompt_panel(
    *,
    finding: ReviewFinding,
    footer: str | None = None,
) -> str:
    """Render the single-finding prompt panel used by inline comments.

    Args:
        finding: Finding the inline comment is anchored to.
        footer: Small-print line under the collapsed body. Defaults to the
            single-finding footer; pass ``""`` to omit it.

    Returns:
        Markdown for the panel.
    """
    return render_agent_prompt_panel(
        findings=(finding,),
        scope=AgentPromptScope(kind=AgentPromptScopeKind.SINGLE_FINDING),
        footer=footer,
    )


def render_sticky_prompt_pointer(*, sticky_url: str) -> str:
    """Render the one-line pointer that replaces a duplicate fix-all panel.

    When this round's findings are exactly the PR's still-open findings, the
    per-review body would repeat the sticky comment's prompt verbatim. It links
    to the sticky instead, so there is never a wrong prompt to copy.

    Args:
        sticky_url: URL of the sticky status comment.

    Returns:
        Markdown for the pointer line.
    """
    return (
        "⚡ Fix prompt: identical to the "
        f"[sticky comment's fix-all]({sticky_url}) this round — copy it there."
    )
