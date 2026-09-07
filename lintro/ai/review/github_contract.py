"""The one contract every GitHub review comment obeys.

Two comments are posted for a review — the sticky mission-control board
(``sticky/``) and the failure surface (``github_errors.py``) — and
before this module they enforced the size invariant twice, differently: the
sticky pruned section by section and reserved room for a trailing state block,
while the error path sliced the string at the cap and hoped. Same invariant,
two implementations, and the bug #1866 fixed occurred in both (epic #1974).

Everything a comment body must satisfy before it reaches the GitHub API now
lives here, and both paths import it:

* :class:`CommentBudget` — how many characters the body may use, and how many
  are reserved for a trailer (the hidden state block) that will be appended
  after the body is rendered.
* :func:`fit_body` — shrink a rendered body into that budget by dropping
  history, then resolved findings, then open findings, each with a visible
  marker so nothing is ever dropped silently.
* :func:`cap_body` — the last-resort hard truncation, with its own notice, for
  a body a single pathological section blew past pruning.
* :func:`fit_body_with_state` — fit, reserve, and append the state block so
  the concatenation is always inside the budget.
* :func:`render_state_block` / :func:`parse_state_block` and
  :func:`sanitize_comment_text`, re-exported so a caller reaches for the
  contract rather than for whichever module happens to define them.

The module is pure: it renders and measures strings and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from loguru import logger

from lintro.ai.review.github_constants import (
    ARCHIVE_MARKER,
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STICKY_MARKER,
)
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.review_state_codec import (
    decode_state,
    prune_state_to_fit,
    render_state_block,
)
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "ARCHIVE_MARKER",
    "DEFAULT_BUDGET",
    "MAX_COMMENT_CHARS",
    "STATE_MARKER_PREFIX",
    "STATE_MARKER_SUFFIX",
    "STICKY_MARKER",
    "TRUNCATION_NOTICE",
    "Assembler",
    "CommentBudget",
    "RenderLimits",
    "SectionCounts",
    "cap_body",
    "fit_body",
    "fit_body_with_state",
    "parse_state_block",
    "render_state_block",
    "sanitize_comment_text",
]

#: Visible marker left in place of the text :func:`cap_body` removes. Pruning
#: in :func:`fit_body` marks its own drops; this covers the case pruning could
#: not reach.
TRUNCATION_NOTICE = "\n\n> ✂️ Comment truncated to fit GitHub's size limit."

#: Upper bound on the binary search in :func:`largest_fitting`. A section with
#: more entries than this is searched only over its first slice, which is far
#: more than any comment can render anyway.
PRUNE_SEARCH_CEILING = 4096


@dataclass(frozen=True, kw_only=True, slots=True)
class CommentBudget:
    """How much room a comment body has, after reserving room for a trailer.

    Attributes:
        max_chars: Total characters the posted comment may occupy, body plus
            trailer. Defaults to the sticky comment's soft budget, which sits
            below GitHub's hard 65,536-character limit.
        reserved: Characters already claimed by a trailer that will be
            concatenated after the body — in practice the hidden state block.
            Negative values are treated as zero.
    """

    max_chars: int = MAX_COMMENT_CHARS
    reserved: int = 0

    @property
    def body_limit(self) -> int:
        """Return the characters available to the visible body alone.

        Returns:
            int: ``max_chars`` less the reservation, never below zero.
        """
        return max(self.max_chars - max(self.reserved, 0), 0)

    def reserving(self, *, chars: int) -> CommentBudget:
        """Return the same budget with a trailer reservation applied.

        Args:
            chars: Characters the trailer will occupy.

        Returns:
            CommentBudget: A copy whose ``reserved`` is ``chars``.
        """
        return replace(self, reserved=chars)


#: The budget every posting path starts from: the full soft cap, nothing
#: reserved. Frozen, so sharing one instance is safe.
DEFAULT_BUDGET = CommentBudget()


@dataclass(frozen=True, kw_only=True, slots=True)
class RenderLimits:
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


@dataclass(frozen=True, kw_only=True, slots=True)
class SectionCounts:
    """How many entries each prunable section actually has.

    Bounding the pruning search by the real counts rather than by a fixed
    constant keeps a three-finding round at a couple of renders instead of a
    dozen.

    Attributes:
        prior_runs: Prior runs available to the history table.
        open: Open findings available to the open-findings section.
        resolved: Resolved findings available to the resolved section.
    """

    prior_runs: int
    open: int
    resolved: int


class Assembler(Protocol):
    """Callable that renders a whole comment body at given section limits."""

    def __call__(self, *, limits: RenderLimits) -> str:
        """Render the body.

        Args:
            limits: Per-section render limits to apply.

        Returns:
            str: The assembled body, without any trailing state block.
        """
        ...  # pragma: no cover - structural type only


def cap_body(*, body: str, budget: CommentBudget = DEFAULT_BUDGET) -> str:
    """Hard-truncate an over-long body as the final size safety net.

    Section-aware pruning in :func:`fit_body` handles every realistic
    overflow. This exists so a pathological single section (one enormous
    finding title, say) or a body with no prunable sections at all — the
    error surface — can never produce a comment GitHub rejects. The budget's
    reservation leaves room for a trailing state block (#1866).

    Args:
        body: Comment body without any trailing state block.
        budget: Budget the body must fit inside.

    Returns:
        str: The body unchanged when it fits, else truncated with a visible
        notice.
    """
    limit = budget.body_limit
    if len(body) <= limit:
        return body
    keep = max(limit - len(TRUNCATION_NOTICE), 0)
    return body[:keep].rstrip() + TRUNCATION_NOTICE


def largest_fitting(
    *,
    assemble: Assembler,
    limits: RenderLimits,
    field: str,
    ceiling: int,
    budget: CommentBudget = DEFAULT_BUDGET,
    minimum: int = 0,
) -> str | None:
    """Binary-search the largest value of one limit whose body still fits.

    Both prunable finding sections order newest-first, so capping their count
    drops the oldest entries — the same oldest-first policy the run history
    follows.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        limits: Limits already applied to the cheaper sections.
        field: Name of the :class:`RenderLimits` field to search over.
        ceiling: Number of entries the section actually has.
        budget: Budget the rendered body must fit inside.
        minimum: Smallest count the section may be rendered at. Sections whose
            absence would hollow out the comment pass ``1`` so the search can
            never settle on showing none of them.

    Returns:
        str | None: The body rendered at the largest fitting count, or
        ``None`` when not even ``minimum`` entries of that section make the
        body fit.
    """
    limit = budget.body_limit
    best: str | None = None
    lower, upper = minimum, min(ceiling, PRUNE_SEARCH_CEILING)
    while lower <= upper:
        middle = (lower + upper) // 2
        candidate = assemble(limits=replace(limits, **{field: middle}))
        if len(candidate) <= limit:
            best = candidate
            lower = middle + 1
        else:
            upper = middle - 1
    return best


def fit_body(
    *,
    assemble: Assembler,
    counts: SectionCounts,
    budget: CommentBudget = DEFAULT_BUDGET,
) -> str:
    """Shrink the rendered body until it fits the budget left for it.

    Pruning order is deliberate: history is the least valuable content on the
    comment, resolved findings are already fixed, and open findings are what a
    reader is actually here for, so they are trimmed last. Each stage leaves a
    visible marker, so nothing is ever dropped silently.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        counts: How many entries each prunable section has.
        budget: Budget the body must fit inside, including any reservation
            held back for a trailing state block (#1866).

    Returns:
        str: A body at or under the remaining budget when that is reachable by
        pruning, else the smallest body pruning can produce, hard-truncated as
        a last resort.
    """
    limit = budget.body_limit
    limits = RenderLimits()
    body = assemble(limits=limits)
    if len(body) <= limit:
        return body

    # 1. Drop the oldest run history first, one round at a time.
    for history in range(counts.prior_runs - 1, -1, -1):
        limits = replace(limits, history=history)
        body = assemble(limits=limits)
        if len(body) <= limit:
            return body

    # 2. Then the oldest resolved findings — they are already fixed.
    fitted = largest_fitting(
        assemble=assemble,
        limits=limits,
        field="resolved",
        ceiling=counts.resolved,
        budget=budget,
    )
    if fitted is not None:
        return fitted

    # 3. Finally the open findings, keeping as many as fit. A verdict with no
    # substance is worse than an over-long comment the final cap will trim, so
    # the search floor is one finding, and one is still rendered when even that
    # overflows.
    limits = replace(limits, resolved=0)
    fitted = largest_fitting(
        assemble=assemble,
        limits=limits,
        field="open",
        ceiling=counts.open,
        budget=budget,
        minimum=1,
    )
    if fitted is None:
        fitted = assemble(limits=replace(limits, open=1))
    return cap_body(body=fitted, budget=budget)


def fit_body_with_state(
    *,
    assemble: Assembler,
    counts: SectionCounts,
    state: ReviewState,
    budget: CommentBudget = DEFAULT_BUDGET,
) -> str:
    """Fit the visible body, then append the state block inside the budget.

    Fits the visible body first, prunes and renders the state block against
    that body, and, when needed, refits the body with the block's length
    reserved so the final concatenation always fits the budget (#1866).

    Authoritative state moved to workflow artifacts in #2154, so
    :func:`render_state_block` renders nothing today and the trailer is empty
    in every live path. The reserve invariant still lives here rather than
    being deleted piecemeal: it is the contract a comment obeys, and the
    posting paths must not each re-derive it the next time a trailer exists.

    Args:
        assemble: Callable taking ``limits`` and returning the rendered body.
        counts: How many entries each prunable section has.
        state: State to embed in the hidden trailing block.
        budget: Budget the body *and* the state block must fit inside
            together.

    Returns:
        str: Complete comment body including the state block.
    """
    body = fit_body(assemble=assemble, counts=counts, budget=budget)
    state_block = _pruned_state_block(state=state, body=body, budget=budget)
    if len(body) + len(state_block) > budget.max_chars:
        # Body left no room for even the pruned-down state; refit with an
        # explicit reservation so appending the block cannot overflow.
        reserved = budget.reserving(chars=len(state_block))
        body = fit_body(assemble=assemble, counts=counts, budget=reserved)
        state_block = _pruned_state_block(state=state, body=body, budget=budget)
    if len(body) + len(state_block) > budget.max_chars:
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
        capped = cap_body(body=body, budget=budget.reserving(chars=len(empty_block)))
        return capped + empty_block
    return body + state_block


def _pruned_state_block(
    *,
    state: ReviewState,
    body: str,
    budget: CommentBudget,
) -> str:
    """Render the state block pruned to whatever the body left over.

    Args:
        state: State to embed.
        body: Visible body the block will be appended to.
        budget: Budget the concatenation must fit inside.

    Returns:
        str: The rendered state block.
    """
    return render_state_block(
        state=prune_state_to_fit(state=state, body=body, limit=budget.max_chars),
    )


def parse_state_block(*, body: str) -> ReviewState:
    """Decode the review state a comment body carries in its hidden block.

    v1 blobs are migrated in place; a missing, malformed, or unknown-version
    blob yields an empty state rather than raising, and a forged marker in
    visible prose loses to the last well-formed block.

    Args:
        body: Existing comment body.

    Returns:
        ReviewState: The decoded review state.
    """
    return decode_state(body=body)
