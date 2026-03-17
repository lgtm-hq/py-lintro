"""Session cost budget tracker."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class CostBudget:
    """Track cumulative AI session cost against an optional ceiling.

    Attributes:
        max_cost_usd: Maximum total cost in USD per AI session.
            None disables the limit.
    """

    max_cost_usd: float | None = None
    _spent: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def record(self, cost: float) -> None:
        """Record a cost increment."""
        with self._lock:
            self._spent += cost

    @property
    def spent(self) -> float:
        """Return total cost spent so far."""
        with self._lock:
            return self._spent

    @property
    def remaining(self) -> float | None:
        """Return remaining budget, or None if unlimited."""
        if self.max_cost_usd is None:
            return None
        return max(0.0, self.max_cost_usd - self.spent)

    def check(self) -> None:
        """Raise AIError if the budget has been exceeded."""
        if self.max_cost_usd is not None and self.spent >= self.max_cost_usd:
            from lintro.ai.exceptions import AIError

            raise AIError(
                f"AI cost budget exceeded: ${self.spent:.4f} spent, "
                f"limit is ${self.max_cost_usd:.2f}",
            )
