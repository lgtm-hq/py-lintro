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
        NOT_PR: Not a ``--pr`` run; a branch or working-tree review has
            no pull-request head to anchor a delta on.
        SAME_HEAD: The prior round reviewed this very commit.
        BASE_MOVED: The base branch was merged into the branch since the
            prior round, so ``prior..head`` would carry base-authored
            lines as the PR's.
        DELTA_FAILED: git could not compute the range; the round read
            the whole diff rather than a delta it never got.
        NO_PRIOR_HEAD: The prior round recorded no head commit.
        NOT_ANCESTOR: The prior head is not an ancestor of this head — a
            force-push or a rewritten branch.
        NO_TREE: The run has no repository to compute a range in
            (:attr:`~lintro.ai.review.enums.review_checkout.ReviewCheckout.NONE`).
        EXPLICIT_FULL: ``--full`` asked for the whole diff.
    """

    DELTA = auto()
    FIRST_ROUND = auto()
    NOT_PR = auto()
    SAME_HEAD = auto()
    BASE_MOVED = auto()
    DELTA_FAILED = auto()
    NO_PRIOR_HEAD = auto()
    NOT_ANCESTOR = auto()
    NO_TREE = auto()
    EXPLICIT_FULL = auto()
