"""What a review round reads: the whole diff, or the delta since last round."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.delta_reason import DeltaReason

__all__ = ["DeltaPlan"]


@dataclass(frozen=True, slots=True)
class DeltaPlan:
    """The scope decision for one round (#2627).

    A delta round narrows what the chunk calls *read*: the embedded diff of a
    queued file is ``since_sha..head`` rather than the whole pull-request
    change to that file. It never narrows what may be *reported*, and it
    never changes the coverage identity of a file (the whole-PR patch hash),
    so resume and the ``INCOMPLETE`` rule of ADR-0007 are unaffected.

    Attributes:
        reason: Why this scope was chosen; :attr:`DeltaReason.DELTA` is the
            only value under which ``since_sha`` is set.
        since_sha: The prior round's head commit, or ``None`` on a full round.
    """

    reason: DeltaReason
    since_sha: str | None = None

    @property
    def is_delta(self) -> bool:
        """Whether the round reads only the delta.

        Returns:
            True when ``since_sha`` anchors a delta range.
        """
        return self.reason is DeltaReason.DELTA and self.since_sha is not None

    @classmethod
    def full(cls, *, reason: DeltaReason) -> DeltaPlan:
        """A whole-diff round for the given reason.

        Args:
            reason: Why the round is full; never :attr:`DeltaReason.DELTA`.

        Returns:
            The plan.
        """
        return cls(reason=reason, since_sha=None)
