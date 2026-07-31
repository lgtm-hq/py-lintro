"""Session cost budget tracker."""

from __future__ import annotations

import asyncio
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
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _async_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _async_lock_loop: asyncio.AbstractEventLoop | None = field(
        default=None,
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
        """Raise ``AICostBudgetExceededError`` if the budget has been exceeded."""
        with self._lock:
            if self.max_cost_usd is not None and self._spent >= self.max_cost_usd:
                self._raise_exceeded()

    def _raise_exceeded(self) -> None:
        from lintro.ai.exceptions import AICostBudgetExceededError

        raise AICostBudgetExceededError(
            f"AI cost budget exceeded: ${self._spent:.4f} spent, "
            f"limit is ${self.max_cost_usd:.2f}",
        )

    def _get_async_lock(self) -> asyncio.Lock:
        """Return the ``asyncio.Lock`` bound to the running event loop.

        An ``asyncio.Lock`` is tied to the loop it is awaited on, so the
        lock is rebuilt whenever the budget is used from a different loop
        (e.g. a tool plugin driving its own ``asyncio.run``).

        Returns:
            The lock guarding budgeted execution on the current loop.
        """
        loop = asyncio.get_running_loop()
        if self._async_lock is None or self._async_lock_loop is not loop:
            self._async_lock = asyncio.Lock()
            self._async_lock_loop = loop
        return self._async_lock

    async def execute(
        self,
        fn: Callable[[], Awaitable[_T]],
        *,
        cost_of: Callable[[_T], float],
    ) -> _T:
        """Await ``fn`` under the budget lock to prevent parallel overspend.

        The gate is an ``asyncio.Lock``, never the threading lock: holding
        a threading lock across an ``await`` would stall every other task
        on the loop.

        Args:
            fn: Coroutine function performing the budgeted call.
            cost_of: Extracts the incurred cost from ``fn``'s result.

        Returns:
            Whatever ``fn`` returned.
        """
        async with self._get_async_lock():
            with self._lock:
                if self.max_cost_usd is not None and self._spent >= self.max_cost_usd:
                    self._raise_exceeded()
            result = await fn()
            with self._lock:
                self._spent += cost_of(result)
            return result
