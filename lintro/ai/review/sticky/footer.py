"""The sticky footer, worded from what a round actually posted inline (#3410).

The footer tells a reader where finding detail lives. A round whose findings
were all routed to the sticky — P3 nits, below-floor notes, lines outside the
diff, or a rejected inline batch — posted no inline comment, so a footer that
always says "commented inline" sends the reader looking for threads that do
not exist.
"""

from __future__ import annotations

from lintro.ai.review.github_constants import (
    STICKY_FOOTER,
    STICKY_FOOTER_NEUTRAL,
    STICKY_HOW_TO_READ,
)
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.posting_tiers import inline_tier_findings
from lintro.ai.review.sticky.cells import _plural

__all__ = ["inline_split", "round_footer", "sticky_footer"]


def inline_split(
    *,
    result: ReviewResult,
    inline_failure: InlinePostFailure | None,
) -> tuple[int, int]:
    """Count this round's findings with and without an inline comment.

    A finding has an inline comment when the posting policy and the severity
    tier route it to a thread and it is not among the findings that could not
    be posted (unmappable to the diff, or in a rejected batch).

    Args:
        result: The round's review result.
        inline_failure: Findings whose inline comments could not be posted.

    Returns:
        ``(inline, sticky_only)`` counts, summing to the round's findings.
    """
    failed = 0 if inline_failure is None else inline_failure.count
    candidates = len(inline_tier_findings(findings=result.findings))
    inline = max(candidates - failed, 0)
    return inline, len(result.findings) - inline


def sticky_footer(*, inline: int, sticky_only: int) -> str:
    """Word the footer for a round's inline and sticky-only finding counts.

    Args:
        inline: Findings that have an inline comment.
        sticky_only: Findings listed in the sticky comment only.

    Returns:
        The footer line. It claims inline comments only when some exist.
    """
    if not inline and not sticky_only:
        return STICKY_FOOTER_NEUTRAL
    if not sticky_only:
        return STICKY_FOOTER
    if not inline:
        claim = "findings are listed above, none commented inline"
    else:
        claim = (
            f"{inline} {_plural(count=inline, noun='finding')} commented inline · "
            f"{sticky_only} listed above only"
        )
    return f"<sub>🤖 lintro review · {claim} · {STICKY_HOW_TO_READ}</sub>"


def round_footer(
    *,
    result: ReviewResult,
    inline_failure: InlinePostFailure | None,
    inline_pending: bool = False,
) -> str:
    """Render the footer of a sticky built from a completed round.

    The sticky is written before the inline review is posted, and a later
    refresh can be refused. While the inline review is not confirmed, a
    footer that names inline comments would claim delivery that may never
    happen, so it falls back to the neutral wording. Under-claiming is the
    safe failure.

    Args:
        result: The round's review result.
        inline_failure: Findings whose inline comments could not be posted.
        inline_pending: True when the inline review has not been confirmed
            posted yet.

    Returns:
        The footer line for that round.
    """
    inline, sticky_only = inline_split(result=result, inline_failure=inline_failure)
    if inline_pending and inline:
        return STICKY_FOOTER_NEUTRAL
    return sticky_footer(inline=inline, sticky_only=sticky_only)
