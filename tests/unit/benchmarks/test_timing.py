"""Unit tests for benchmark timing math.

These tests exercise the pure statistics helpers only; they never run a real
benchmark. ``measure_command`` is tested against a fast, deterministic command
(``true``) so no long-running subprocess is spawned.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from benchmarks.harness.timing import measure_command, summarize


def test_summarize_single_value_has_zero_stddev() -> None:
    """A single sample yields zero standard deviation and equal min/max/median."""
    stats = summarize([0.5])

    assert_that(stats.runs).is_equal_to(1)
    assert_that(stats.min_s).is_equal_to(0.5)
    assert_that(stats.max_s).is_equal_to(0.5)
    assert_that(stats.median_s).is_equal_to(0.5)
    assert_that(stats.mean_s).is_equal_to(0.5)
    assert_that(stats.stddev_s).is_equal_to(0.0)


def test_summarize_odd_count_median_is_middle_value() -> None:
    """The median of an odd-length list is the middle element."""
    stats = summarize([0.3, 0.1, 0.2])

    assert_that(stats.median_s).is_equal_to(0.2)
    assert_that(stats.min_s).is_equal_to(0.1)
    assert_that(stats.max_s).is_equal_to(0.3)
    assert_that(stats.runs).is_equal_to(3)


def test_summarize_even_count_median_is_mean_of_middle_pair() -> None:
    """The median of an even-length list averages the two central values."""
    stats = summarize([0.1, 0.2, 0.3, 0.4])

    assert_that(stats.median_s).is_close_to(0.25, tolerance=1e-9)
    assert_that(stats.mean_s).is_close_to(0.25, tolerance=1e-9)


def test_summarize_median_is_robust_to_outlier() -> None:
    """The median ignores an extreme outlier that would skew the mean."""
    stats = summarize([0.10, 0.11, 0.12, 0.13, 10.0])

    assert_that(stats.median_s).is_equal_to(0.12)
    assert_that(stats.mean_s).is_greater_than(stats.median_s)


def test_summarize_preserves_samples() -> None:
    """The raw samples are retained on the stats object."""
    stats = summarize([0.1, 0.2])

    assert_that(stats.samples).is_equal_to([0.1, 0.2])


def test_summarize_empty_raises() -> None:
    """Summarizing an empty list raises ValueError."""
    assert_that(summarize).raises(ValueError).when_called_with([])


def test_summarize_negative_raises() -> None:
    """A negative duration is rejected."""
    assert_that(summarize).raises(ValueError).when_called_with([0.1, -0.2])


def test_measure_command_records_runs_and_exit_codes() -> None:
    """Measuring a trivial command yields one stat and exit code per run."""
    measurement = measure_command(["true"], runs=3, warmup=1)

    assert_that(measurement.stats.runs).is_equal_to(3)
    assert_that(measurement.exit_codes).is_length(3)
    assert_that(measurement.exit_codes).contains_only(0)
    assert_that(measurement.warmup_runs).is_equal_to(1)
    assert_that(measurement.stats.min_s).is_greater_than_or_equal_to(0.0)


def test_measure_command_captures_nonzero_exit() -> None:
    """A failing command's exit code is captured, not raised."""
    measurement = measure_command(["false"], runs=2, warmup=0)

    assert_that(measurement.exit_codes).is_length(2)
    assert_that(measurement.exit_codes).contains_only(1)


@pytest.mark.parametrize(
    ("runs", "warmup"),
    [(0, 1), (-1, 0)],
)
def test_measure_command_invalid_runs_raise(runs: int, warmup: int) -> None:
    """Invalid run counts raise ValueError before executing anything."""
    assert_that(measure_command).raises(ValueError).when_called_with(
        ["true"],
        runs=runs,
        warmup=warmup,
    )


def test_measure_command_negative_warmup_raises() -> None:
    """A negative warmup count raises ValueError."""
    assert_that(measure_command).raises(ValueError).when_called_with(
        ["true"],
        runs=1,
        warmup=-1,
    )
