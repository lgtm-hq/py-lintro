"""Tests for the profiling suggestions engine."""

from __future__ import annotations

from assertpy import assert_that

from lintro.profiling.suggestions import get_suggestions
from lintro.profiling.timer import ToolTiming


def test_no_timings_yields_no_suggestions() -> None:
    """An empty timing list produces no suggestions."""
    assert_that(get_suggestions([])).is_empty()


def test_slowest_tool_percentage_reported() -> None:
    """The slowest tool is surfaced with its share of total time."""
    timings = [
        ToolTiming(tool="mypy", duration=8.0),
        ToolTiming(tool="ruff", duration=2.0),
    ]

    suggestions = get_suggestions(timings)

    assert_that(suggestions[0]).is_equal_to("mypy is slowest (80% of total time)")


def test_mypy_hint_only_when_mypy_ran() -> None:
    """The mypy-specific hint appears only when mypy is present."""
    with_mypy = get_suggestions([ToolTiming(tool="mypy", duration=1.0)])
    without_mypy = get_suggestions([ToolTiming(tool="ruff", duration=1.0)])

    assert_that(any("dmypy" in s for s in with_mypy)).is_true()
    assert_that(any("dmypy" in s for s in without_mypy)).is_false()


def test_slow_threshold_flags_secondary_tools() -> None:
    """A non-slowest tool over the threshold gets its own warning."""
    timings = [
        ToolTiming(tool="mypy", duration=12.0),
        ToolTiming(tool="bandit", duration=6.0),
        ToolTiming(tool="ruff", duration=0.4),
    ]

    suggestions = get_suggestions(timings, slow_threshold=5.0)

    assert_that(any("bandit took 6.00s" in s for s in suggestions)).is_true()
    # The slowest tool is not double-reported via the threshold path.
    assert_that(any("mypy took" in s for s in suggestions)).is_false()


def test_darglint_deprecation_hint() -> None:
    """Darglint gets a deprecation/replacement hint when it ran."""
    suggestions = get_suggestions([ToolTiming(tool="darglint", duration=3.0)])

    assert_that(any("pydoclint" in s for s in suggestions)).is_true()
