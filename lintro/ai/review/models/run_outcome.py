"""What one review round concluded about the pull request."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.review_verdict import ReviewVerdict

__all__ = ["CONVERGENCE_SCORE_PRECISION", "NARRATIVE_LIMIT", "RunOutcome"]

#: Decimal places a persisted convergence score is rounded to. It lives with
#: the field it rounds rather than in :mod:`lintro.ai.review.convergence`,
#: which imports this module; the scoring module re-exports it as
#: ``SCORE_PRECISION`` so every surface rounds a score exactly once, the same
#: way.
CONVERGENCE_SCORE_PRECISION = 2

#: Maximum characters of a stored per-round narrative, on the way in (it is
#: persisted in the state blob, which competes for the same size cap) and on
#: the way out. It is a property of the stored field rather than of any one
#: renderer, so writer and reader cannot drift to different caps.
NARRATIVE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Findings, verdict and recap produced by one AI review round.

    Three of the fields are deliberately optional rather than zero-defaulted:
    a record persisted before they existed must render as "unknown", because a
    fabricated zero would claim the round fixed nothing, left nothing open, or
    converged completely.

    Attributes:
        verdict: Readiness verdict derived from open findings after this round.
        confidence: Aggregate confidence label reported for the round.
        p1: Count of P1 findings reported in this round.
        p2: Count of P2 findings reported in this round.
        p3: Count of P3 findings reported in this round.
        questions: Count of entries reported as questions rather than
            findings in this round (#1925). Excluded from ``p1``/``p2``/``p3``
            and from the derived verdict.
        downgraded: Count of P1 findings the evidence gate downgraded to P2 in
            this round (#1925). Recorded per run so severity inflation, and
            how much of it the gate absorbed, stays visible over time rather
            than being an invisible parse-time edit.
        resolved: Number of findings this round resolved. ``None`` on a record
            persisted before the field existed — history renders that as ``—``
            rather than as a fabricated zero, which would read as "this round
            fixed nothing".
        open_after: Number of findings still open *after* this round, which is
            what a reader of the history actually wants to know. ``None`` on a
            legacy record, where only the raised count was ever stored.
        narrative: One-line recap of the round in the model's own words, taken
            from the structured summary headline (or the review summary's first
            sentence). Empty when the model produced neither.
        convergence_score: Aggregate convergence score over the findings still
            open after this round (#2099), or ``None`` on a record persisted
            before scoring existed. Serialized only when present, so a legacy
            record re-encodes with no new keys and a missing score reads as
            "not measured" rather than as a fabricated ``0.0`` — which would
            claim the round was quiet.
    """

    verdict: ReviewVerdict = ReviewVerdict.READY
    confidence: str = ""
    p1: int = 0
    p2: int = 0
    p3: int = 0
    questions: int = 0
    downgraded: int = 0
    resolved: int | None = None
    open_after: int | None = None
    narrative: str = ""
    convergence_score: float | None = None
