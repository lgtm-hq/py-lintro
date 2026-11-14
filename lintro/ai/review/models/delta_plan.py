"""What a review round reads: the whole diff, or the delta since last round."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from lintro.ai.review.enums.delta_reason import DeltaReason

__all__ = [
    "LOCATION_BASE_MOVED",
    "LOCATION_HOLDS",
    "LOCATION_UNCHECKED",
    "LOCATION_UNMAPPED",
    "LOCATION_UNPLACED",
    "DeltaPlan",
]

#: A relocation's ``line_sha`` when git could not reach the record's head and
#: the pull request's rename metadata did not move it (#3413). The record is
#: kept where it was stored, and its path leaving the diff is no fix.
LOCATION_UNMAPPED = "unmapped"

#: A relocation's ``line_sha`` for a record beyond the round's mapping cap
#: (:data:`lintro.ai.review.delta_lines.MAX_MAPPED_FINDINGS`): kept where it
#: was stored, carried unverified and never resolved that round (#3413).
LOCATION_UNCHECKED = "unchecked"

#: A relocation's ``line_sha`` for a record whose merge-base moved since its
#: head and whose line now lies outside the pull request's diff (#3467): the
#: diff gate would drop any re-report there, so it is kept where it was
#: stored, carried unverified and never resolved that round.
LOCATION_BASE_MOVED = "base-moved"

#: A relocation's ``line_sha`` for a record still on its own path whose
#: in-place evidence is missing (#3466): git reads the file as rewritten
#: (``git diff -B`` dissimilarity of 50 or more, or no answer), since the
#: record's head or within a delta, or the record has no recorded head. An
#: edited or deleted line in a file that was not rewritten stays in place.
#: Kept where it was stored; its absence resolves it only on the
#: moved-finding check's "fixed" verdict.
LOCATION_UNPLACED = "unplaced"

#: The relocation ``line_sha`` values that hold a record in place.
LOCATION_HOLDS = frozenset(
    {LOCATION_UNMAPPED, LOCATION_UNCHECKED, LOCATION_BASE_MOVED, LOCATION_UNPLACED},
)


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
        open_lines: On a delta round, the lines of the prior open findings per
            file mapped to ``since_sha``'s coordinates, or ``None`` for a file
            holding a finding whose line could not be mapped there (#3403).
            A narrowed file must show every one of them or be read whole.
        relocations: ``(key, path, line, line_sha)`` for each open record
            whose stored location was not in ``since_sha``'s coordinates; the
            matcher judges the record there and persists it (#3403). A
            ``line_sha`` in :data:`LOCATION_HOLDS` keeps the record where it
            was stored instead (#3413).
    """

    reason: DeltaReason
    since_sha: str | None = None
    open_lines: Mapping[str, tuple[int, ...] | None] = field(default_factory=dict)
    relocations: tuple[tuple[str, str, int, str], ...] = ()

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
