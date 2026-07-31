"""Session cost budget tracker."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

_T = TypeVar("_T")


@dataclass
class CostBudget:
    """Track cumulative AI session cost against an optional ceiling.

    Attributes:
        max_cost_usd: Maximum total cost in USD per AI session.
            None disables the limit.
    """

    max_cost_usd: float | None = None
    _spent: float = field(default=0.0, init=False)
    _reserved: float = field(default=0.0, init=False)
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
        """Return total cost actually spent so far."""
        with self._lock:
            return self._spent

    @property
    def reserved(self) -> float:
        """Return the cost currently reserved by in-flight budgeted calls."""
        with self._lock:
            return self._reserved

    @property
    def remaining(self) -> float | None:
        """Return remaining budget, or None if unlimited."""
        if self.max_cost_usd is None:
            return None
        with self._lock:
            return max(0.0, self.max_cost_usd - self._spent - self._reserved)

    def check(self) -> None:
        """Raise ``AICostBudgetExceededError`` if the budget has been exceeded.

        In-flight reservations count against the ceiling, so a call that
        starts while concurrent budgeted calls have already committed the
        remaining budget is rejected up front.
        """
        with self._lock:
            self._raise_if_exhausted_locked()

    def _raise_if_exhausted_locked(self) -> None:
        """Raise when committed cost meets the ceiling. Caller holds ``_lock``."""
        if (
            self.max_cost_usd is not None
            and self._spent + self._reserved >= self.max_cost_usd
        ):
            self._raise_exceeded()

    def _raise_exceeded(self) -> None:
        from lintro.ai.exceptions import AICostBudgetExceededError

        raise AICostBudgetExceededError(
            f"AI cost budget exceeded: ${self._spent:.4f} spent, "
            f"${self._reserved:.4f} reserved, "
            f"limit is ${self.max_cost_usd:.2f}",
        )

    def _reserve(self, estimate: float) -> None:
        """Check the ceiling and reserve an estimated cost.

        Args:
            estimate: Conservative pre-call cost estimate in USD.
        """
        with self._lock:
            self._raise_if_exhausted_locked()
            self._reserved += estimate

    def _release(self, estimate: float) -> None:
        """Drop a reservation without charging anything.

        Args:
            estimate: The amount previously reserved.
        """
        with self._lock:
            self._reserved = max(0.0, self._reserved - estimate)

    def _settle(self, *, estimate: float, actual: float) -> None:
        """Replace a reservation with the actual incurred cost.

        Args:
            estimate: The amount previously reserved.
            actual: The cost the completed call actually incurred.
        """
        with self._lock:
            self._reserved = max(0.0, self._reserved - estimate)
            self._spent += actual

    async def execute(
        self,
        fn: Callable[[], Awaitable[_T]],
        *,
        cost_of: Callable[[_T], float],
        estimate: float = 0.0,
    ) -> _T:
        """Await ``fn`` under the budget without serializing provider calls.

        Uses a reserve/reconcile pattern: the ceiling check and the
        reservation happen together under the (never-awaited-across)
        threading lock, ``fn`` is awaited with no lock held so budgeted
        calls stay concurrent, and the reservation is reconciled with the
        real cost afterwards. A failed call releases its reservation so a
        provider error never permanently consumes budget.

        Overspend bound: the ceiling can only be overshot by the cost of
        the calls already in flight when the last affordable call started,
        minus whatever they reserved. Callers that pass no ``estimate``
        reserve nothing, so with ``n`` concurrent calls the session may
        exceed ``max_cost_usd`` by up to ``n - 1`` calls' cost; passing a
        conservative ``estimate`` shrinks that window to the estimate error.

        Args:
            fn: Coroutine function performing the budgeted call.
            cost_of: Extracts the incurred cost from ``fn``'s result.
            estimate: Conservative pre-call cost estimate in USD to reserve
                for the duration of the call. Defaults to ``0.0`` (check
                only) for callers with no usable estimate.

        Returns:
            Whatever ``fn`` returned.

        Raises:
            BaseException: Whatever ``fn`` raised, after releasing the
                reservation. ``AICostBudgetExceededError`` is raised
                before ``fn`` runs when the ceiling is already committed.
        """
        self._reserve(estimate)
        try:
            result = await fn()
            actual = cost_of(result)
        except BaseException:
            self._release(estimate)
            raise
        self._settle(estimate=estimate, actual=actual)
        return result
