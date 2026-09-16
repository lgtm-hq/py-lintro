"""How much of a pull request one review round actually looked at."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RunCoverage"]


@dataclass(frozen=True, slots=True)
class RunCoverage:
    """Reach of one AI review round over the changed diff.

    Two independent axes of incompleteness live here and must not be
    conflated: ``partial`` means the round stopped before every chunk was
    reviewed, while ``coverage_limited`` means every chunk was reviewed but
    not at full depth.

    Attributes:
        files_reviewed: Number of changed files included in the review.
        files_skipped: Number of changed files excluded from the review.
        checks: Number of checklist items in the prompt.
        partial: True when the review stopped before every chunk was reviewed.
        coverage_limited: True when an output-exhaustion split, a cut file
            diff, a lost split half or a failed depth pass may have suppressed
            findings in this round (#2003); an incomplete synthesis pass does
            not set it (#2702). A
            separate axis from ``partial``: every chunk was reviewed, but not
            at full depth. Serialized only when True, so a record written
            before the field existed round-trips byte-identically and keeps
            rendering as an unlimited round.
        chunks_reviewed: Number of chunks actually reviewed.
        chunks_total: Total number of chunks in the diff.
        synthesis_degraded: True when the round's cross-chunk synthesis pass
            was truncated or failed (#2704). A narrative degradation, not a
            coverage gap: it is kept so the run history can show it, and it
            never keys the convergence guard or forces another round, since
            under the #2702 semantics per-file coverage is complete and a
            large PR would otherwise re-review forever. Serialized only when
            True, like ``coverage_limited``.
    """

    files_reviewed: int = 0
    files_skipped: int = 0
    checks: int = 0
    partial: bool = False
    coverage_limited: bool = False
    chunks_reviewed: int = 0
    chunks_total: int = 0
    synthesis_degraded: bool = False
