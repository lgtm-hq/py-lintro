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
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from types import FrameType

from loguru import logger

from lintro.ai.exceptions import AIProviderError

__all__ = [
    "SIGTERM_TIMEOUT_MESSAGE",
    "PostInterrupted",
    "SigtermDeferral",
    "defer_sigterm",
    "install_review_interrupt",
    "sigterm_timeout_error",
]

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


class PostInterrupted(BaseException):
    """Raised out of the post when the first SIGTERM lands while it runs.

    A ``BaseException`` so a broad ``except Exception`` in the posting code
    cannot swallow it: the post has to stop so the output can be written
    inside the runner's SIGKILL window.
    """


@dataclass(slots=True)
class SigtermDeferral:
    """SIGTERM handling for the post-and-render tail of ``lintro review``.

    The JSON envelope is written after posting (#3426), and the CI wrapper
    classifies the round from it, so SIGTERM must never kill the process
    before the envelope is complete. The wrapper and ``uv run`` both forward
    the signal, so it can arrive more than once.

    The first SIGTERM is recorded. Inside :meth:`abortable` it also stops the
    post, so the output is written within the runner's SIGKILL window rather
    than after a slow post. Anywhere else, rendering included, it only
    records the request. Every later SIGTERM is ignored, like
    :func:`install_review_interrupt`'s handler does. :func:`defer_sigterm`
    exits ``128 + SIGTERM`` once the output is out.

    Attributes:
        received: True once a SIGTERM arrived.
        aborting: True while a SIGTERM should stop the work in progress.
    """

    received: bool = False
    aborting: bool = False

    def handle(self, signum: int, frame: FrameType | None) -> None:
        """Record the first SIGTERM and ignore the rest.

        Args:
            signum: The received signal number.
            frame: Current stack frame, unused.

        Raises:
            PostInterrupted: On the first SIGTERM while :meth:`abortable`
                is active.
        """
        del signum, frame
        if self.received:
            return
        self.received = True
        logger.warning(
            "Review received SIGTERM; writing the output before exiting",
        )
        if self.aborting:
            self.aborting = False
            raise PostInterrupted

    @contextmanager
    def abortable(self) -> Iterator[None]:
        """Let the first SIGTERM stop the enclosed work.

        Yields:
            None: Control returns to the caller.

        Raises:
            PostInterrupted: When a SIGTERM was already recorded, so the
                work is skipped.
        """
        if self.received:
            raise PostInterrupted
        self.aborting = True
        try:
            yield
        finally:
            self.aborting = False


@contextmanager
def defer_sigterm() -> Iterator[SigtermDeferral]:
    """Hold SIGTERM off until the enclosed output is written and flushed.

    On exit, standard output and error are flushed. If a SIGTERM was
    received, the process then exits ``128 + SIGTERM``, the status the
    signal would have given it. The deferring handler stays installed until
    then, so a repeated signal cannot kill the process on the way out.
    Otherwise the default handler is restored.

    A handler someone else installed is left alone, and so is a call off
    the main thread, where Python cannot install one.

    Yields:
        SigtermDeferral: The deferral state, whose :meth:`~SigtermDeferral.abortable`
        marks the work a SIGTERM may stop.

    Raises:
        SystemExit: ``128 + SIGTERM`` when a SIGTERM was received.
    """
    deferral = SigtermDeferral()
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        yield deferral
        return
    try:
        signal.signal(signal.SIGTERM, deferral.handle)
    except ValueError:
        yield deferral
        return
    try:
        yield deferral
    except PostInterrupted:
        pass
    finally:
        for stream in (sys.stdout, sys.stderr):
            with suppress(OSError, ValueError):
                stream.flush()
        if deferral.received:
            raise SystemExit(128 + signal.SIGTERM)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
