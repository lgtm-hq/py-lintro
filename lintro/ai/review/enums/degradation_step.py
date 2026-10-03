"""Which step of a review round a coverage degradation came from (#2803)."""

from __future__ import annotations

from enum import StrEnum, auto

from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)

__all__ = ["DegradationStep", "step_for_reason"]


class DegradationStep(StrEnum):
    """The step a persisted degradation belongs to.

    A rerun decides per step whether the degradation still stands: a step
    this round ran again answers for itself, and a step it did not run
    carries the earlier answer forward (#2803).

    Attributes:
        CHUNK: A chunk's main pass (split, cut, lost half, turn limit, the
            delegated-diff fallback).
        ADVERSARIAL_SWEEP: A chunk's depth-3 adversarial sweep.
        QUESTION_PASS: The once-per-run per-PR question pass.
        SYNTHESIS: The whole-PR synthesis pass.
        VERIFICATION: The verification pass.
        RUN: A fact about the whole run's setup (no tree for the agent),
            recomputed by every round.
    """

    CHUNK = auto()
    ADVERSARIAL_SWEEP = auto()
    QUESTION_PASS = auto()
    SYNTHESIS = auto()
    VERIFICATION = auto()
    RUN = auto()


_STEP_BY_REASON: dict[CoverageDegradationReason, DegradationStep] = {
    CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED: DegradationStep.CHUNK,
    CoverageDegradationReason.DIFF_TRUNCATED: DegradationStep.CHUNK,
    CoverageDegradationReason.SPLIT_HALF_FAILED: DegradationStep.CHUNK,
    CoverageDegradationReason.TURN_LIMIT_REACHED: DegradationStep.CHUNK,
    CoverageDegradationReason.DELEGATED_DIFF_EMBEDDED: DegradationStep.CHUNK,
    CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED: (
        DegradationStep.ADVERSARIAL_SWEEP
    ),
    CoverageDegradationReason.GENERATED_QUESTIONS_FAILED: (
        DegradationStep.QUESTION_PASS
    ),
    CoverageDegradationReason.SYNTHESIS_TRUNCATED: DegradationStep.SYNTHESIS,
    CoverageDegradationReason.SYNTHESIS_FAILED: DegradationStep.SYNTHESIS,
    CoverageDegradationReason.VERIFICATION_FAILED: DegradationStep.VERIFICATION,
    CoverageDegradationReason.NO_TREE_FOR_AGENT: DegradationStep.RUN,
}


def step_for_reason(*, reason: CoverageDegradationReason) -> DegradationStep:
    """Return the step a degradation reason is recorded against.

    Args:
        reason: The recorded reason.

    Returns:
        The step; every reason has one (a test pins the mapping as total).
    """
    return _STEP_BY_REASON[reason]
