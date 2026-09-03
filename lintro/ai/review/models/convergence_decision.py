"""Outcome of the in-code re-review stop rule (#2099)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ConvergenceDecision"]


@dataclass(frozen=True, slots=True)
class ConvergenceDecision:
    """Whether another review round would be redundant, and why.

    Built by :func:`lintro.ai.review.convergence.evaluate_convergence` from
    persisted run records alone, so it is a pure function of review history:
    the same state always yields the same decision.

    Attributes:
        converged: True when the stop rule fired and no provider call should
            be made this round.
        round_number: Round that was skipped. Zero when nothing was skipped.
        score: Most recent recorded convergence score, or ``None`` when the
            latest window round was never measured or the rule did not run.
            Never a fabricated zero.
        threshold: Configured threshold, or ``None`` when the rule is
            disabled.
        stable_rounds: How many consecutive quiet rounds were required.
        trajectory: Every recorded score, oldest first. Carried even on a
            non-converged decision so surfaces can render the stability signal
            without re-deriving it.
    """

    converged: bool = False
    round_number: int = 0
    score: float | None = None
    threshold: float | None = None
    stable_rounds: int = 0
    trajectory: tuple[float, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision for the machine-readable review outcome.

        Returns:
            JSON-serializable mapping describing the skipped round.
        """
        return {
            "round": self.round_number,
            "score": self.score,
            "threshold": self.threshold,
            "stable_rounds": self.stable_rounds,
            "trajectory": list(self.trajectory),
        }
