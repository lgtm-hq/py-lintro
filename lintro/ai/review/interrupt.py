"""SIGTERM/SIGINT handling so a killed review can persist coverage (#2156).

GitHub Actions sends SIGTERM to the review step process group (~5–7 s
before SIGKILL). The Cursor ``agent`` CLI has also been observed to
``killpg`` its own group, which would take lintro with it unless the
child is started in a new session. This module turns the first signal
into a cooperative stop so the orchestrator can write ``state.json``.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from contextlib import suppress

from loguru import logger

from lintro.ai.exceptions import AIProviderError

SIGTERM_TIMEOUT_MESSAGE = "review timed out after SIGTERM"


def sigterm_timeout_error() -> AIProviderError:
    """Return the persistable timeout error used for a review interrupt.

    Returns:
        An ``AIProviderError`` classified as ``TIMEOUT`` (``timed out``)
        and chained from ``TimeoutError`` so the existing mid-round
        persist path treats it like a CLI timeout.
    """
    error = AIProviderError(SIGTERM_TIMEOUT_MESSAGE)
    error.__cause__ = TimeoutError("SIGTERM")
    return error


def install_review_interrupt(stop: asyncio.Event) -> Callable[[], None]:
    """Request *stop* when the process receives SIGTERM or SIGINT.

    Args:
        stop: Event set by the first signal. The orchestrator waits on
            this and then persists completed chunks.

    Returns:
        A callable that removes the handlers. Safe to call more than once.
    """
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []

    def _request_stop() -> None:
        """Set the stop event once and log that persist should follow."""
        if stop.is_set():
            return
        logger.warning(
            "Review received SIGTERM/SIGINT; persisting coverage and stopping",
        )
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, _request_stop)
            installed.append(sig)

    def _uninstall() -> None:
        """Remove handlers installed by this call."""
        for sig in installed:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
        installed.clear()

    return _uninstall
