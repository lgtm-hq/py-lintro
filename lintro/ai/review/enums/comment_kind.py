"""The kinds of issue comment a review owns on a pull request (#2305)."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["CommentKind"]


class CommentKind(StrEnum):
    """Which of the review's comments a lifecycle decision is about.

    A review owns at most one comment of each kind per pull request, and the
    kind is what the marker in the body identifies. Naming them makes the
    lifecycle decision legible in a log line and lets a caller say what it is
    writing without passing the marker string around.

    Attributes:
        STICKY: The mission-control board, rewritten every round. The error
            and converged surfaces write to this same comment, because a
            failed round is a state of the board rather than a comment of its
            own.
        ARCHIVE: The run-history archive, split off when the board's history
            no longer fits the size budget.
        ERROR: A failure surface posted where no board can be re-rendered.
    """

    STICKY = auto()
    ARCHIVE = auto()
    ERROR = auto()
