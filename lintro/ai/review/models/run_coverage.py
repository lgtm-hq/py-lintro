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
        coverage_limited: True when a CLI findings cap or an output-exhaustion
            retry may have suppressed findings in this round (#2003). A
            separate axis from ``partial``: every chunk was reviewed, but not
            at full depth. Serialized only when True, so a record written
            before the field existed round-trips byte-identically and keeps
            rendering as an unlimited round.
        chunks_reviewed: Number of chunks actually reviewed.
        chunks_total: Total number of chunks in the diff.
    """

    files_reviewed: int = 0
    files_skipped: int = 0
    checks: int = 0
    partial: bool = False
    coverage_limited: bool = False
    chunks_reviewed: int = 0
    chunks_total: int = 0
