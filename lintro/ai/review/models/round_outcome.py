"""What one completed review round decided, derived once and shared."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class RoundOutcome:
    """Matching, verdict and run record for one round.

    Rendering the board and persisting the state are two consumers of one
    decision. Deriving it once means a persisted round and the comment that
    announces it cannot disagree about what happened.

    Attributes:
        prior: State the round started from.
        round_number: 1-based number assigned to this round.
        match: Cross-round matching outcome, comment ids already stamped.
        verdict: Readiness verdict, including the coverage gate.
        open_count: Findings still open after this round.
        run: Run record for this round.
        runs: Retained run records, oldest first, this round last.
        truncated: Whether older runs were dropped from ``runs``.
    """

    prior: ReviewState
    round_number: int
    match: FindingMatchResult
    verdict: ReviewVerdict
    open_count: int
    run: RunRecord
    runs: tuple[RunRecord, ...]
    truncated: bool
