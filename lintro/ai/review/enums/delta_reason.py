"""Why a review round read the whole diff or only the delta (#2627)."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["DeltaReason"]


class DeltaReason(StrEnum):
    """What decided a round's scope.

    Exactly one value is :attr:`DELTA`; every other value names why the round
    fell back to reading the whole pull-request diff. The sticky comment
    renders the reason so a reader knows why a round was bigger than the
    push that triggered it.

    Attributes:
        DELTA: The prior round's head is an ancestor of this head; the round
            reads ``prior..head``.
        FIRST_ROUND: No prior round on record.
        NO_PRIOR_HEAD: The prior round recorded no head commit.
        NOT_ANCESTOR: The prior head is not an ancestor of this head — a
            force-push or a rewritten branch.
        NO_TREE: The run has no repository to compute a range in
            (:attr:`~lintro.ai.review.enums.review_checkout.ReviewCheckout.NONE`).
        EXPLICIT_FULL: ``--full`` asked for the whole diff.
    """

    DELTA = auto()
    FIRST_ROUND = auto()
    NO_PRIOR_HEAD = auto()
    NOT_ANCESTOR = auto()
    NO_TREE = auto()
    EXPLICIT_FULL = auto()
