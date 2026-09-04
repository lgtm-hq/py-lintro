"""Deterministic convergence scoring for ``lintro review`` rounds (#2099).

Cross-round state already tracks *what* each round found; nothing decided
*when another round is wasted tokens*. Every push triggered a full re-review,
and convergence was social — reply-resolve churn on stale findings. This
module is the arithmetic half of the fix: a single number per round that says
how much unresolved risk the open findings still represent, so a stop rule can
be applied in code rather than asked of the model.

The formula is adapted from `JPHutchins/code-review
<https://github.com/JPHutchins/code-review>`_::

    score = floor + (ceiling - floor) * confidence * likelihood

Every input is a field lintro already parses, so the score is a pure function
of the tracked findings: no wall clock, no randomness, no provider call. The
same finding set always scores the same number, on any machine, in any round.

Severity band (floor, ceiling)
    ============ ======= =========
    Severity     Floor   Ceiling
    ============ ======= =========
    ``P1``       6.0     10.0
    ``P2``       3.0     6.0
    ``P3``       0.5     3.0
    ============ ======= =========

    The floor is what the finding is worth even at minimum confidence on the
    weakest evidence — a P1 nobody is sure about still outranks a certain P3.
    The band above the floor is what confidence and likelihood can earn.

Confidence multiplier
    ``high`` 1.0 · ``medium`` 0.6 · ``low`` 0.3. An absent or unrecognized
    label scores as ``medium``: silence is not evidence of certainty, and
    failing to the floor would let a corrupted label deflate the score toward
    a premature stop.

Likelihood proxy (``evidence_style``)
    ``diff_local`` 1.0 · ``cross_file`` 0.8 · ``speculative`` 0.4. This is
    the only place ``evidence_style`` affects a number; it still never
    suppresses or down-ranks a finding on any surface.

Systemic categories
    ``contract-drift`` and ``breaking-change`` are scored at likelihood 1.0
    regardless of the reported evidence style. A broken contract is a fact
    about the interface, not an inference about a hunk, so a speculative
    label must not discount it toward an early stop.

Aggregation
    The round score is the sum over the round's **open** findings.
    Questions (``kind: question``) are excluded, exactly as they are excluded
    from the derived verdict: a question is not unresolved risk. Resolved
    findings contribute nothing, so a round that fixes everything scores 0.0.

Stop rule
    :func:`evaluate_convergence` reports convergence when the last
    ``stable_rounds`` recorded scores are all strictly below ``threshold``.
    A ``partial`` round (planned work left undone) and a ``coverage_limited``
    round (reviewed, but capped) can never attest stability: a low score
    there may simply mean the round never got far enough to find anything, so
    either one in the window blocks the stop. Rounds persisted before scoring
    existed carry no score and are likewise not evidence.
"""

from __future__ import annotations

import math

from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.run_record import (
    CONVERGENCE_SCORE_PRECISION,
    RunRecord,
)
from lintro.enums.review_category import ReviewCategory

__all__ = [
    "CONFIDENCE_MULTIPLIERS",
    "DEFAULT_CONFIDENCE_MULTIPLIER",
    "EVIDENCE_LIKELIHOOD",
    "SCORE_PRECISION",
    "SEVERITY_BANDS",
    "SYSTEMIC_CATEGORIES",
    "SYSTEMIC_LIKELIHOOD",
    "ConvergenceDecision",
    "evaluate_convergence",
    "format_convergence_stamp",
    "format_score",
    "format_trajectory",
    "score_finding",
    "score_records",
    "score_trajectory",
]

#: Floor and ceiling per severity. The floor is the unconditional worth of a
#: finding at that severity; the gap to the ceiling is what confidence and
#: likelihood can earn back.
SEVERITY_BANDS: dict[Severity, tuple[float, float]] = {
    Severity.P1: (6.0, 10.0),
    Severity.P2: (3.0, 6.0),
    Severity.P3: (0.5, 3.0),
}

#: Multiplier applied to the band above the floor, per reported confidence.
CONFIDENCE_MULTIPLIERS: dict[str, float] = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
}

#: Multiplier for an absent or unrecognized confidence label. Deliberately the
#: middle value: an unlabeled finding is not a confident one, but treating it
#: as the weakest would let bad data deflate the score into a premature stop.
DEFAULT_CONFIDENCE_MULTIPLIER: float = CONFIDENCE_MULTIPLIERS["medium"]

#: Likelihood proxy per self-reported evidence basis.
EVIDENCE_LIKELIHOOD: dict[EvidenceStyle, float] = {
    EvidenceStyle.DIFF_LOCAL: 1.0,
    EvidenceStyle.CROSS_FILE: 0.8,
    EvidenceStyle.SPECULATIVE: 0.4,
}

#: Categories whose findings are systemic rather than hunk-local, and are
#: therefore scored at full likelihood whatever evidence style was reported.
SYSTEMIC_CATEGORIES: frozenset[str] = frozenset(
    {
        str(ReviewCategory.CONTRACT_DRIFT),
        str(ReviewCategory.BREAKING_CHANGE),
    },
)

#: Likelihood applied to a systemic category.
SYSTEMIC_LIKELIHOOD: float = 1.0

#: Decimal places used everywhere a score is rendered or persisted, so the
#: sticky, the JSON outcome, and the state blob can never disagree by a digit.
#: Re-exported from the persistence model, which is the one definition.
SCORE_PRECISION: int = CONVERGENCE_SCORE_PRECISION


def _confidence_multiplier(*, confidence: str) -> float:
    """Return the multiplier for a reported confidence label.

    Args:
        confidence: Raw confidence label from the model or the state blob.

    Returns:
        The mapped multiplier, or :data:`DEFAULT_CONFIDENCE_MULTIPLIER` when
        the label is empty or unrecognized.
    """
    return CONFIDENCE_MULTIPLIERS.get(
        confidence.strip().lower(),
        DEFAULT_CONFIDENCE_MULTIPLIER,
    )


def _likelihood(*, category: str, evidence_style: EvidenceStyle) -> float:
    """Return the likelihood proxy for a finding's category and evidence.

    Args:
        category: Finding category label.
        evidence_style: Self-reported evidence basis.

    Returns:
        :data:`SYSTEMIC_LIKELIHOOD` for a systemic category, otherwise the
        mapped evidence likelihood.
    """
    if category.strip().lower() in SYSTEMIC_CATEGORIES:
        return SYSTEMIC_LIKELIHOOD
    return EVIDENCE_LIKELIHOOD[evidence_style]


def score_finding(
    *,
    severity: Severity,
    category: str,
    confidence: str,
    evidence_style: EvidenceStyle = EvidenceStyle.DIFF_LOCAL,
) -> float:
    """Score one finding on the severity band.

    Args:
        severity: Finding severity.
        category: Finding category label; systemic categories score at
            likelihood 1.0.
        confidence: Reported model confidence (``high``/``medium``/``low``).
        evidence_style: Self-reported evidence basis, used as the likelihood
            proxy for non-systemic categories.

    Returns:
        ``floor + (ceiling - floor) * confidence * likelihood``, rounded to
        :data:`SCORE_PRECISION` places so every surface renders the same
        digits.
    """
    floor, ceiling = SEVERITY_BANDS[severity]
    earned = (ceiling - floor) * _confidence_multiplier(confidence=confidence)
    likelihood = _likelihood(category=category, evidence_style=evidence_style)
    return round(floor + earned * likelihood, SCORE_PRECISION)


def score_records(*, records: tuple[FindingRecord, ...]) -> float:
    """Aggregate the convergence score over the open findings in a round.

    Args:
        records: Every tracked finding record for the round, open and
            resolved.

    Returns:
        Sum of :func:`score_finding` over the open, non-question records,
        rounded to :data:`SCORE_PRECISION` places. ``0.0`` when nothing is
        open.
    """
    total = sum(
        score_finding(
            severity=record.severity,
            category=record.category,
            confidence=record.confidence,
            evidence_style=record.evidence_style,
        )
        for record in records
        if record.status is FindingStatus.OPEN
        and record.kind is not FindingKind.QUESTION
    )
    return round(total, SCORE_PRECISION)


def score_trajectory(*, runs: tuple[RunRecord, ...]) -> tuple[float, ...]:
    """Return the recorded score for each round that has one, oldest first.

    Runs persisted before scoring existed carry no score and are skipped, so
    a long-lived PR's trajectory starts where measurement did rather than
    fabricating zeros for history.

    Args:
        runs: Retained run records, oldest first.

    Returns:
        The recorded scores in round order.
    """
    return tuple(
        run.convergence_score for run in runs if run.convergence_score is not None
    )


def format_score(*, score: float) -> str:
    """Render one score at the shared precision.

    Args:
        score: Score to render.

    Returns:
        The score as a fixed-precision decimal string.
    """
    return f"{score:.{SCORE_PRECISION}f}"


def format_trajectory(*, scores: tuple[float, ...]) -> str:
    """Render a score trajectory as an arrow-joined line.

    Args:
        scores: Recorded scores, oldest first.

    Returns:
        For example ``2.40 → 1.80 → 1.20``; empty when no score is recorded.
    """
    return " → ".join(format_score(score=score) for score in scores)


def format_convergence_stamp(*, decision: ConvergenceDecision) -> str:
    """Render the one-sentence convergence stamp.

    The single source for this wording. The sticky banner, the terminal line,
    and the JSON outcome detail all render this string, so no two surfaces can
    describe the same stop differently.

    Args:
        decision: The evaluated decision; must be a converged one.

    Returns:
        For example ``converged at round 5 (score 1.20 < threshold 3.00)``.

    Raises:
        ValueError: When the decision carries no measured score or threshold,
            so a fabricated ``0.00`` can never be rendered.
    """
    if decision.score is None or decision.threshold is None:
        msg = "only a converged decision with a measured score can be stamped"
        raise ValueError(msg)
    return (
        f"converged at round {decision.round_number} "
        f"(score {format_score(score=decision.score)} < threshold "
        f"{format_score(score=decision.threshold)})"
    )


def _stability_window(
    *,
    runs: tuple[RunRecord, ...],
    stable_rounds: int,
) -> tuple[RunRecord, ...]:
    """Return the most recent runs that may attest stability.

    Args:
        runs: Retained run records, oldest first.
        stable_rounds: How many consecutive rounds must agree.

    Returns:
        The last ``stable_rounds`` runs, or an empty tuple when fewer than
        that many exist.
    """
    if len(runs) < stable_rounds:
        return ()
    return tuple(runs[-stable_rounds:])


def _readable_threshold(*, threshold: object) -> float | None:
    """Return the threshold only when it is a usable number.

    The stop rule fails in the dangerous direction — it skips a review — so
    anything it cannot read must disable it rather than be guessed at. A bool
    is rejected explicitly: it is an ``int`` subclass, and ``True`` would
    otherwise arm the rule at a threshold of 1.0.

    Args:
        threshold: Raw ``review.convergence.threshold`` value.

    Returns:
        The threshold as a float, or ``None`` when the rule is disabled or
        the value is not a finite, non-negative number.
    """
    if threshold is None or isinstance(threshold, bool):
        return None
    if not isinstance(threshold, int | float):
        return None
    value = float(threshold)
    # Scores are non-negative and "quiet" means strictly below the threshold,
    # so a zero threshold can never be met: treat it as disabled rather than
    # as a rule that silently never fires.
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def evaluate_convergence(
    *,
    runs: tuple[RunRecord, ...],
    threshold: float | None,
    stable_rounds: int,
) -> ConvergenceDecision:
    """Decide, in code, whether another review round would be redundant.

    Args:
        runs: Retained run records from prior rounds, oldest first.
        threshold: Score strictly below which a round counts as quiet.
            ``None`` — or any value that is not a finite, non-negative
            number — disables the stop rule entirely.
        stable_rounds: How many consecutive quiet rounds are required.

    Returns:
        The decision, carrying the trajectory either way so callers can
        render the signal without re-deriving it. ``converged`` is False
        whenever the rule is disabled, too few rounds exist, any run in the
        window lacks a score, any run in the window was ``partial`` or
        ``coverage_limited``, or any score reaches the threshold.
    """
    trajectory = score_trajectory(runs=runs)
    threshold = _readable_threshold(threshold=threshold)
    # ``bool`` is an ``int`` subclass; ``True`` must not read as a one-round
    # streak any more than it may read as a threshold of one.
    if (
        threshold is None
        or isinstance(stable_rounds, bool)
        or not isinstance(stable_rounds, int)
        or stable_rounds < 1
    ):
        return ConvergenceDecision(trajectory=trajectory)
    window = _stability_window(runs=runs, stable_rounds=stable_rounds)
    if not window:
        return ConvergenceDecision(threshold=threshold, trajectory=trajectory)
    scores = [run.convergence_score for run in window]
    degraded = any(run.partial or run.coverage_limited for run in window)
    quiet = all(
        score is not None and math.isfinite(score) and 0.0 <= score < threshold
        for score in scores
    )
    # ``score`` stays unset when the latest window run was never measured, or
    # was measured to something unusable: a fabricated zero would read as the
    # quietest possible round, and a NaN/inf would render as a nonsense stamp
    # and serialize to invalid JSON on the way to CI (#2099 review).
    latest = scores[-1]
    if latest is not None and not (math.isfinite(latest) and latest >= 0.0):
        latest = None
    return ConvergenceDecision(
        converged=quiet and not degraded,
        round_number=window[-1].round + 1,
        score=latest,
        threshold=threshold,
        stable_rounds=stable_rounds,
        trajectory=trajectory,
    )
