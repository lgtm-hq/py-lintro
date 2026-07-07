"""Fixtures for watch-mode unit tests.

Provides deterministic test doubles so watch behaviour can be exercised
without real timers, filesystems, or sleeps:

* ``fake_timer_factory`` records scheduled callbacks and lets a test fire or
  cancel them explicitly.
* ``fake_tool`` / ``make_tools`` build lightweight tool registries with
  controllable ``file_patterns``.
* ``fake_fs_event`` builds synthetic watchdog events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pytest


@dataclass
class FakeTimer:
    """A recording stand-in for :class:`threading.Timer`.

    Attributes:
        delay: The scheduled delay in seconds.
        callback: The zero-argument callback to run on ``fire``.
        started: Whether ``start`` was called.
        cancelled: Whether ``cancel`` was called.
    """

    delay: float
    callback: Callable[[], None]
    started: bool = False
    cancelled: bool = False

    def start(self) -> None:
        """Mark the timer as started."""
        self.started = True

    def cancel(self) -> None:
        """Mark the timer as cancelled."""
        self.cancelled = True

    def fire(self) -> None:
        """Invoke the scheduled callback as if the timer elapsed."""
        self.callback()


@dataclass
class FakeTimerFactory:
    """Factory that records the timers it creates.

    Attributes:
        timers: All timers created, in order.
    """

    timers: list[FakeTimer] = field(default_factory=list)

    def __call__(self, delay: float, callback: Callable[[], None]) -> FakeTimer:
        """Create and record a :class:`FakeTimer`.

        Args:
            delay: Delay in seconds.
            callback: Callback to run when fired.

        Returns:
            The created fake timer.
        """
        timer = FakeTimer(delay=delay, callback=callback)
        self.timers.append(timer)
        return timer

    @property
    def latest(self) -> FakeTimer:
        """Return the most recently created timer.

        Returns:
            The last timer created.
        """
        return self.timers[-1]


@dataclass
class _FakeDefinition:
    """Minimal tool definition exposing ``file_patterns`` and ``can_fix``."""

    file_patterns: list[str]
    can_fix: bool = False


@dataclass
class _FakePlugin:
    """Minimal tool plugin exposing a ``definition``."""

    definition: _FakeDefinition


@pytest.fixture
def fake_timer_factory() -> FakeTimerFactory:
    """Provide a recording timer factory.

    Returns:
        A fresh :class:`FakeTimerFactory`.
    """
    return FakeTimerFactory()


@pytest.fixture
def make_tools() -> Callable[[dict[str, list[str]]], dict[str, object]]:
    """Return a builder for fake tool registries.

    Returns:
        A callable mapping ``{tool_name: [patterns]}`` to a registry dict of
        fake plugins suitable for the tool-selection functions.
    """

    def _build(spec: dict[str, list[str]]) -> dict[str, object]:
        return {
            name: _FakePlugin(definition=_FakeDefinition(file_patterns=patterns))
            for name, patterns in spec.items()
        }

    return _build


@dataclass
class FakeFsEvent:
    """A synthetic watchdog filesystem event.

    Attributes:
        src_path: Source path of the event.
        is_directory: Whether the event targets a directory.
        dest_path: Optional destination path (for move events).
    """

    src_path: str
    is_directory: bool = False
    dest_path: str | None = None


@pytest.fixture
def fake_fs_event() -> Callable[..., FakeFsEvent]:
    """Return a builder for synthetic filesystem events.

    Returns:
        A callable building :class:`FakeFsEvent` instances.
    """

    def _build(
        src_path: str,
        *,
        is_directory: bool = False,
        dest_path: str | None = None,
    ) -> FakeFsEvent:
        return FakeFsEvent(
            src_path=src_path,
            is_directory=is_directory,
            dest_path=dest_path,
        )

    return _build
