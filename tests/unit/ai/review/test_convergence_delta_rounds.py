"""Convergence is unaffected by whether a round was a delta or a full read.

Carried from #2627 PR 1 (#2754, design list): a delta round narrows what the
model reads, never how the round is scored, so the convergence stop rule must
decide exactly as it would on full rounds with the same findings. On-request
reviews (#2795) mix full, delta and targeted rounds on one PR, which is what
makes this worth pinning.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.convergence import evaluate_convergence, score_trajectory
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord

_SCORES = (4.0, 0.3, 0.2, 0.1)


def _history(*, delta_rounds: bool) -> tuple[RunRecord, ...]:
    """Return four rounds with the same scores, delta or full from round two.

    Args:
        delta_rounds: Whether rounds two onward were delta reads.

    Returns:
        The run records, oldest first.
    """
    runs = []
    for index, score in enumerate(_SCORES, start=1):
        if index == 1:
            coverage = RunCoverage(delta_reason=str(DeltaReason.FIRST_ROUND))
        elif delta_rounds:
            coverage = RunCoverage(
                delta_since=f"sha{index - 1}",
                delta_reason=str(DeltaReason.DELTA),
            )
        else:
            coverage = RunCoverage(delta_reason=str(DeltaReason.EXPLICIT_FULL))
        runs.append(
            RunRecord(
                identity=RunIdentity(round=index, sha=f"sha{index}", model="claude"),
                coverage=coverage,
                outcome=RunOutcome(convergence_score=score),
            ),
        )
    return tuple(runs)


def test_the_trajectory_is_the_same_for_delta_and_full_rounds() -> None:
    """The recorded scores do not depend on how much each round read."""
    assert_that(score_trajectory(runs=_history(delta_rounds=True))).is_equal_to(
        score_trajectory(runs=_history(delta_rounds=False)),
    )


@pytest.mark.parametrize(
    ("threshold", "stable_rounds"),
    [(0.5, 2), (0.5, 3), (0.15, 2), (None, 2)],
    ids=["stops-after-two", "stops-after-three", "never-quiet-enough", "disabled"],
)
def test_the_stop_decision_is_the_same_for_delta_and_full_rounds(
    threshold: float | None,
    stable_rounds: int,
) -> None:
    """Delta rounds neither hasten nor delay the convergence stop.

    Args:
        threshold: The quiet-score threshold.
        stable_rounds: Consecutive quiet rounds required.
    """
    delta = evaluate_convergence(
        runs=_history(delta_rounds=True),
        threshold=threshold,
        stable_rounds=stable_rounds,
    )
    full = evaluate_convergence(
        runs=_history(delta_rounds=False),
        threshold=threshold,
        stable_rounds=stable_rounds,
    )

    assert_that(delta).is_equal_to(full)
