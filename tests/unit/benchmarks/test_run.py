"""Tests for the benchmark entry point's report-note helpers."""

from __future__ import annotations

from assertpy import assert_that

from benchmarks.harness.results import ScenarioResult
from benchmarks.harness.timing import summarize
from benchmarks.run import _exit_code_notes


def _result(*, label: str, exit_codes: list[int]) -> ScenarioResult:
    """Build a minimal scenario result for note tests.

    Args:
        label: Runner label for the result.
        exit_codes: Exit codes to attach.

    Returns:
        ScenarioResult: A populated result with placeholder timing stats.
    """
    return ScenarioResult(
        tool="lintro",
        label=label,
        fixture="small-python",
        scenario="full_check_warm",
        stats=summarize([0.5, 0.5, 0.5]),
        exit_codes=exit_codes,
        command=["true"],
    )


def test_exit_code_notes_empty_for_clean_runs() -> None:
    """All-zero exit codes produce no notes."""
    results = [_result(label="lintro", exit_codes=[0, 0, 0])]
    assert_that(_exit_code_notes(results)).is_empty()


def test_exit_code_notes_flags_nonzero_runs() -> None:
    """A non-zero exit code yields a note naming the runner and codes."""
    results = [
        _result(label="lintro", exit_codes=[0, 0, 0]),
        _result(label="sequential-native", exit_codes=[0, 2, 0]),
    ]
    notes = _exit_code_notes(results)
    assert_that(notes).is_length(1)
    assert_that(notes[0]).contains("sequential-native")
    assert_that(notes[0]).contains("small-python/full_check_warm")
    assert_that(notes[0]).contains("[0, 2, 0]")
