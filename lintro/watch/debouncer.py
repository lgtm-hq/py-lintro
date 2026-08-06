"""Debounce rapid filesystem change events into batched runs.

A single "save" in an editor, or a bulk operation like ``git checkout``,
can emit many filesystem events in quick succession. The :class:`Debouncer`
collects changed paths and only invokes the batch callback once the stream
of events has been quiet for ``delay_ms`` milliseconds. Each new event
resets the timer, so a continuous burst yields exactly one run when it
settles.

The timer is created through an injectable factory so the timing behaviour
can be exercised deterministically in tests without ``sleep``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Debouncer", "TimerLike"]

DEFAULT_DELAY_MS: int = 300


class TimerLike(Protocol):
    """Minimal timer interface used by :class:`Debouncer`.

    Matches the subset of :class:`threading.Timer` the debouncer relies on,
    allowing a fake timer to be injected in tests.
    """

    def start(self) -> None:
        """Start the timer."""
        ...

    def cancel(self) -> None:
        """Cancel the timer if it has not fired yet."""
        ...


def _default_timer_factory(
    delay: float,
    callback: Callable[[], None],
) -> TimerLike:
    """Create a daemon :class:`threading.Timer`.

    Args:
        delay: Delay in seconds before ``callback`` fires.
        callback: Zero-argument function invoked when the timer elapses.

    Returns:
        A started-capable daemon timer.
    """
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    return timer


class Debouncer:
    """Coalesce rapid change events into a single debounced batch.

    Thread-safety:
        All mutation of the pending set and the active timer is guarded by
        an internal lock, so :meth:`on_change` may be called from watchdog's
        observer thread while :meth:`flush` / :meth:`cancel` are called from
        another thread.

    Attributes:
        delay_ms: Quiet period, in milliseconds, before the batch fires.
    """

    def __init__(
        self,
        *,
        callback: Callable[[set[str]], None],
        delay_ms: int = DEFAULT_DELAY_MS,
        timer_factory: Callable[[float, Callable[[], None]], TimerLike] | None = None,
    ) -> None:
        """Initialize the debouncer.

        Args:
            callback: Invoked with the set of changed paths once the change
                stream has been quiet for ``delay_ms`` milliseconds.
            delay_ms: Debounce interval in milliseconds. Must be >= 0.
            timer_factory: Optional factory that builds a timer given a delay
                in seconds and a callback. Defaults to a daemon
                :class:`threading.Timer`. Injectable for deterministic tests.

        Raises:
            ValueError: If ``delay_ms`` is negative.
        """
        if delay_ms < 0:
            msg = f"delay_ms must be >= 0, got {delay_ms}"
            raise ValueError(msg)

        self.delay_ms = delay_ms
        self._callback = callback
        self._timer_factory = timer_factory or _default_timer_factory
        self._pending: set[str] = set()
        self._timer: TimerLike | None = None
        self._lock = threading.Lock()

    @property
    def pending(self) -> set[str]:
        """Return a snapshot of paths queued for the next batch.

        Returns:
            A copy of the currently pending paths.
        """
        with self._lock:
            return set(self._pending)

    def on_change(self, path: str) -> None:
        """Record a changed path and (re)arm the debounce timer.

        Args:
            path: Filesystem path that changed.
        """
        with self._lock:
            self._pending.add(path)
            self._reset_timer_locked()

    def _reset_timer_locked(self) -> None:
        """Cancel any active timer and start a fresh one.

        The caller must hold ``self._lock``.
        """
        if self._timer is not None:
            self._timer.cancel()
        self._timer = self._timer_factory(self.delay_ms / 1000.0, self._fire)
        self._timer.start()

    def _fire(self) -> None:
        """Timer callback: emit the pending batch if non-empty."""
        with self._lock:
            batch = set(self._pending)
            self._pending.clear()
            self._timer = None
        if batch:
            self._callback(batch)

    def flush(self) -> None:
        """Immediately emit any pending batch, bypassing the timer.

        Useful on shutdown to avoid dropping the final set of changes, and
        for deterministic testing.
        """
        self._fire_now()

    def _fire_now(self) -> None:
        """Cancel the timer and emit the pending batch synchronously."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            batch = set(self._pending)
            self._pending.clear()
        if batch:
            self._callback(batch)

    def cancel(self) -> None:
        """Discard any pending batch and cancel the timer without firing."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()
