"""AI enhancement result dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AIResult:
    """Structured result of an AI enhancement run.

    Returned by ``run_ai_enhancement`` so callers can influence
    the process exit code based on AI outcomes.

    Attributes:
        fixes_applied: Number of AI fixes successfully applied.
        fixes_failed: Number of AI fixes that failed to apply.
        unfixed_issues: Number of issues that remain unfixed after AI.
        budget_exceeded: Whether the cost budget was exceeded.
        error: Whether an AI error occurred during the run.
    """

    fixes_applied: int = 0
    fixes_failed: int = 0
    unfixed_issues: int = 0
    budget_exceeded: bool = False
    error: bool = False
