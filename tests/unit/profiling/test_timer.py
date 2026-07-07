"""Tests for the profiling Timer and ToolTiming primitives."""

from __future__ import annotations

import time

from assertpy import assert_that

from lintro.profiling.timer import Timer, ToolTiming


def test_timer_measures_elapsed_time() -> None:
    """Timer records a positive duration covering the sleep interval."""
    with Timer() as timer:
        time.sleep(0.02)

    assert_that(timer.duration).is_greater_than_or_equal_to(0.02)


def test_timer_duration_is_monotonic() -> None:
    """A longer workload yields a larger measured duration."""
    with Timer() as short:
        time.sleep(0.01)
    with Timer() as long:
        time.sleep(0.05)

    assert_that(long.duration).is_greater_than(short.duration)


def test_timer_returns_self_on_enter() -> None:
    """The context manager binds the Timer instance to the ``as`` target."""
    timer = Timer()
    with timer as entered:
        pass

    assert_that(entered).is_same_as(timer)


def test_timer_records_duration_even_on_exception() -> None:
    """Duration is populated even when the block raises."""
    timer = Timer()
    try:
        with timer:
            time.sleep(0.01)
            raise ValueError("boom")
    except ValueError:
        pass

    assert_that(timer.duration).is_greater_than(0.0)


def test_tool_timing_defaults() -> None:
    """ToolTiming defaults files_checked and issues_found to zero."""
    timing = ToolTiming(tool="ruff", duration=1.5)

    assert_that(timing.tool).is_equal_to("ruff")
    assert_that(timing.duration).is_equal_to(1.5)
    assert_that(timing.files_checked).is_equal_to(0)
    assert_that(timing.issues_found).is_equal_to(0)
