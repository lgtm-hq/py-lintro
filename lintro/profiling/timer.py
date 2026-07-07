"""Timing primitives for lintro performance profiling.

This module provides a monotonic wall-clock timer and the per-tool timing
record used to build the ``--profile`` report and its JSON payload.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import TracebackType


@dataclass
class ToolTiming:
    """Per-tool timing attribution for a single profiled run.

    Attributes:
        tool: Display name of the tool.
        duration: Wall-clock execution time in seconds.
        files_checked: Number of distinct files the tool reported issues on.
        issues_found: Number of issues the tool reported.
    """

    tool: str
    duration: float
    files_checked: int = field(default=0)
    issues_found: int = field(default=0)


class Timer:
    """Context manager that measures monotonic wall-clock time.

    Uses :func:`time.perf_counter` so the measurement is monotonic and
    unaffected by wall-clock adjustments. The elapsed time in seconds is
    available on :attr:`duration` after the ``with`` block exits.

    Example:
        >>> with Timer() as timer:
        ...     do_work()
        >>> timer.duration  # seconds elapsed
    """

    def __init__(self) -> None:
        """Initialize the timer with zeroed start and duration."""
        self.start: float = 0.0
        self.duration: float = 0.0

    def __enter__(self) -> Timer:
        """Start the timer.

        Returns:
            Timer: This timer instance.
        """
        self.start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the timer and record the elapsed duration.

        Args:
            exc_type: Exception type if one was raised in the block.
            exc_val: Exception instance if one was raised in the block.
            exc_tb: Traceback if an exception was raised in the block.
        """
        self.duration = time.perf_counter() - self.start
