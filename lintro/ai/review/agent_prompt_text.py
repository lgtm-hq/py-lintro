"""Text and title helpers shared by the agent-prompt bodies and panels.

Split out of :mod:`lintro.ai.review.agent_prompts` (#2301). The wrapping,
fencing, pluralization, scope-sentence and panel-title rules live here so the
prompt bodies and the panels that wrap them share one copy. Every function was
moved verbatim, so prompt bytes are unchanged.
"""

from __future__ import annotations

import re
import textwrap

from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope

__all__ = [
    "CONTINUATION_INDENT",
    "FOOTERS",
    "WRAP_WIDTH",
    "fence_for",
    "panel_title",
    "plural",
    "scope_sentence",
    "wrap",
]

#: Column at which prompt prose is soft-wrapped inside the fenced code block.
WRAP_WIDTH = 80

#: Indent applied to continuation lines under a finding bullet.
CONTINUATION_INDENT = "  "

_BACKTICK_RUN_RE = re.compile(r"`+")

FOOTERS: dict[AgentPromptScopeKind, str] = {
    AgentPromptScopeKind.ALL_OPEN: (
        "Regenerated every run · covers exactly the open table above"
    ),
    AgentPromptScopeKind.THIS_REVIEW: (
        "For everything still open across all rounds, use the sticky comment's "
        "fix-all prompt"
    ),
    AgentPromptScopeKind.SINGLE_FINDING: (
        "Paste into Claude Code, Cursor, or any coding agent"
    ),
}

_MISSINGFOOTERS = set(AgentPromptScopeKind) - set(FOOTERS)
if _MISSINGFOOTERS:  # pragma: no cover - guards a future scope kind
    raise RuntimeError(
        f"AgentPromptScopeKind members without a default footer: {_MISSINGFOOTERS}",
    )


def plural(*, count: int, noun: str) -> str:
    """Format a count with a naively pluralized noun.

    Args:
        count: Number of items.
        noun: Singular noun to pluralize with a trailing ``s``.

    Returns:
        The count and noun, e.g. ``"1 finding"`` or ``"3 findings"``.
    """
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def wrap(*, text: str, initial_indent: str = "", subsequent_indent: str = "") -> str:
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
        width=WRAP_WIDTH,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def fence_for(*, text: str) -> str:
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


def scope_sentence(*, scope: AgentPromptScope, count: int) -> str:
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
            f"Scope: the {plural(count=count, noun='finding')} posted in {where} of "
            "this PR's lintro review ONLY (older open findings are covered by the "
            "fix-all prompt on the sticky status comment)."
        )
    # Exhaustiveness guard twin to FOOTERS' _MISSINGFOOTERS check: a new
    # AgentPromptScopeKind added without a branch here must fail loudly
    # instead of silently falling through to THIS_REVIEW-flavored text.
    raise ValueError(f"Unhandled AgentPromptScopeKind: {scope.kind!r}")


def panel_title(*, scope: AgentPromptScope, count: int) -> str:
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
        noun = plural(count=count, noun="still-open finding")
        quantifier = noun if count == 1 else f"all {noun}"
        return f"Fix-all prompt — {quantifier}{rounds}"
    if scope.kind is AgentPromptScopeKind.THIS_REVIEW:
        return f"Fix prompt — this round's {plural(count=count, noun='finding')} only"
    # Exhaustiveness guard twin to FOOTERS' _MISSINGFOOTERS check: a new
    # AgentPromptScopeKind added without a branch here must fail loudly
    # instead of silently falling through to THIS_REVIEW-flavored text.
    raise ValueError(f"Unhandled AgentPromptScopeKind: {scope.kind!r}")
