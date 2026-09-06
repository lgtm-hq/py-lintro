"""Tests for severity tallying and the count-delta rendering helpers."""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.models.core.severity_counts import SeverityCounts, SeverityDelta
from lintro.models.core.tool_result import ToolResult
from lintro.utils.severity_counts import (
    count_severities,
    counts_color,
    delta_color,
    format_counts_line,
    format_delta_line,
)


class _Issue:
    """Minimal issue double exposing only ``get_severity``."""

    def __init__(self, level: SeverityLevel) -> None:
        """Store the severity this issue reports.

        Args:
            level: Severity the double returns.
        """
        self._level = level

    def get_severity(self) -> SeverityLevel:
        """Return the configured severity.

        Returns:
            SeverityLevel: The severity this double was built with.
        """
        return self._level


def _result(*levels: SeverityLevel) -> ToolResult:
    """Build a tool result carrying issues of the given severities.

    Args:
        *levels: Severity for each issue on the result.

    Returns:
        ToolResult: Result whose issues report those severities.
    """
    issues: list[Any] = [_Issue(level) for level in levels]
    return ToolResult(
        name="ruff",
        success=not levels,
        issues_count=len(levels),
        issues=issues,
    )


def test_count_severities_tallies_every_result() -> None:
    """Issues are counted across every result and bucketed by severity."""
    counts = count_severities(
        [
            _result(SeverityLevel.ERROR, SeverityLevel.WARNING),
            _result(SeverityLevel.INFO, SeverityLevel.ERROR),
        ],
    )

    assert_that(counts).is_equal_to(SeverityCounts(errors=2, warnings=1, info=1))
    assert_that(counts.total).is_equal_to(4)


def test_count_severities_skips_results_without_issues() -> None:
    """Skipped and clean results contribute nothing."""
    counts = count_severities(
        [
            ToolResult(
                name="ruff",
                success=True,
                skipped=True,
                skip_reason="no files matched",
            ),
            ToolResult(name="black", success=True, issues=[]),
        ],
    )

    assert_that(counts).is_equal_to(SeverityCounts())


def test_count_severities_ignores_non_result_objects() -> None:
    """Anything that is not result-shaped is skipped rather than raising."""
    assert_that(count_severities([object()])).is_equal_to(SeverityCounts())


def test_count_severities_ignores_issues_without_severity() -> None:
    """An issue that cannot report a severity is skipped, not guessed at."""

    class _Opaque:
        """Issue double with no ``get_severity`` method."""

    issues: list[Any] = [_Opaque()]
    result = ToolResult(name="ruff", success=False, issues_count=1, issues=issues)

    assert_that(count_severities([result])).is_equal_to(SeverityCounts())


def test_counts_to_dict_includes_the_total() -> None:
    """The JSON payload carries each severity plus the total."""
    payload = SeverityCounts(errors=1, warnings=2, info=3).to_dict()

    assert_that(payload).is_equal_to(
        {"error": 1, "warning": 2, "info": 3, "total": 6},
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"error": "many"},
        {"error": True},
        {"unrelated": 5},
        {"error": -3, "warning": -1},
    ],
    ids=["empty", "non-int", "bool", "unknown-key", "negative"],
)
def test_counts_from_dict_degrades_to_zero(data: dict[str, object]) -> None:
    """A malformed baseline payload reads as "nothing recorded".

    Args:
        data: Baseline payload to parse.
    """
    assert_that(SeverityCounts.from_dict(data)).is_equal_to(SeverityCounts())


def test_counts_round_trip_through_a_dict() -> None:
    """Serialized counts parse back to the same value."""
    counts = SeverityCounts(errors=4, warnings=0, info=9)

    assert_that(SeverityCounts.from_dict(counts.to_dict())).is_equal_to(counts)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (
            SeverityCounts(errors=1, warnings=2, info=0),
            "Issues: 1 error, 2 warnings, 0 info",
        ),
        (
            SeverityCounts(errors=2, warnings=1, info=1),
            "Issues: 2 errors, 1 warning, 1 info",
        ),
        (SeverityCounts(), "Issues: 0 errors, 0 warnings, 0 info"),
    ],
    ids=["singular-error", "singular-warning", "all-zero"],
)
def test_format_counts_line_pluralizes_each_severity(
    counts: SeverityCounts,
    expected: str,
) -> None:
    """One issue reads singular; anything else plural; ``info`` never changes.

    Args:
        counts: Tallies to render.
        expected: The line the renderer must produce.
    """
    assert_that(format_counts_line(counts)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (SeverityCounts(), "green"),
        (SeverityCounts(info=1), "yellow"),
        (SeverityCounts(warnings=3), "yellow"),
        (SeverityCounts(errors=1, warnings=3), "red"),
    ],
    ids=["clean", "info-only", "warnings-only", "errors"],
)
def test_counts_color_follows_the_worst_severity(
    counts: SeverityCounts,
    expected: str,
) -> None:
    """Green when clean, red on any error, yellow otherwise.

    Args:
        counts: Tallies to colour.
        expected: Expected console colour.
    """
    assert_that(counts_color(counts)).is_equal_to(expected)


def test_format_delta_line_lists_only_what_moved_with_signs() -> None:
    """Severities that did not change are left out of the line."""
    delta = SeverityDelta(errors=-12, warnings=3, info=0)

    assert_that(format_delta_line(delta)).is_equal_to(
        "Change since last run: -12 errors, +3 warnings",
    )


def test_format_delta_line_reports_an_unchanged_run() -> None:
    """A run with no movement says so instead of listing zeroes."""
    assert_that(format_delta_line(SeverityDelta())).is_equal_to(
        "Change since last run: no change",
    )


def test_format_delta_line_pluralizes_a_single_issue() -> None:
    """A delta of one reads singular in both directions."""
    assert_that(format_delta_line(SeverityDelta(errors=-1))).is_equal_to(
        "Change since last run: -1 error",
    )
    assert_that(format_delta_line(SeverityDelta(warnings=1))).is_equal_to(
        "Change since last run: +1 warning",
    )


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (SeverityDelta(errors=-12), "green"),
        (SeverityDelta(errors=3), "red"),
        (SeverityDelta(), "cyan"),
        (SeverityDelta(errors=-1, warnings=1), "green"),
        (SeverityDelta(errors=1, warnings=-1), "red"),
        (SeverityDelta(warnings=-2, info=2), "green"),
        (SeverityDelta(warnings=2), "red"),
        (SeverityDelta(warnings=-2), "green"),
        (SeverityDelta(info=1), "red"),
        (SeverityDelta(info=-1), "green"),
    ],
    ids=[
        "fewer-errors",
        "more-errors",
        "unchanged",
        "error-traded-for-warning",
        "warning-traded-for-error",
        "warning-traded-for-info",
        "more-warnings-only",
        "fewer-warnings-only",
        "more-info-only",
        "fewer-info-only",
    ],
)
def test_delta_color_tracks_the_direction_of_improvement(
    delta: SeverityDelta,
    expected: str,
) -> None:
    """Fewer issues is green even though the arithmetic sign is negative.

    Args:
        delta: Change to colour.
        expected: Expected console colour.
    """
    assert_that(delta_color(delta)).is_equal_to(expected)


def test_delta_between_subtracts_the_previous_run() -> None:
    """``between`` is ``current - previous`` for every severity."""
    delta = SeverityDelta.between(
        current=SeverityCounts(errors=2, warnings=5, info=0),
        previous=SeverityCounts(errors=14, warnings=2, info=3),
    )

    assert_that(delta).is_equal_to(SeverityDelta(errors=-12, warnings=3, info=-3))
    assert_that(delta.total).is_equal_to(-12)
    assert_that(delta.to_dict()).is_equal_to(
        {"error": -12, "warning": 3, "info": -3, "total": -12},
    )


def test_counts_from_dict_keeps_valid_keys_beside_invalid_ones() -> None:
    """A partly-corrupt payload keeps the fields that are still readable."""
    parsed = SeverityCounts.from_dict({"error": 4, "warning": "many", "info": -2})

    assert_that(parsed).is_equal_to(SeverityCounts(errors=4))


def test_count_severities_tallies_issues_on_a_successful_result() -> None:
    """A tool that reports success can still carry advisory findings."""
    issues: list[Any] = [_Issue(SeverityLevel.WARNING), _Issue(SeverityLevel.INFO)]
    result = ToolResult(name="ruff", success=True, issues_count=2, issues=issues)

    assert_that(count_severities([result])).is_equal_to(
        SeverityCounts(warnings=1, info=1),
    )


def test_count_severities_ignores_a_count_only_result() -> None:
    """A result carrying only ``issues_count`` contributes nothing.

    Some synthetic results (the duplicate-code ratchet, certain post-check
    failures) set ``issues_count`` without a parsed ``issues`` list. The
    severity tally is built from real ``BaseIssue`` severities, so it reads
    zero for that shape while ``total_issues`` and the exit code still reflect
    the failure. Pinned here so the contract is explicit rather than
    accidental.
    """
    result = ToolResult(name="pylint", success=False, issues_count=7, issues=None)

    assert_that(count_severities([result])).is_equal_to(SeverityCounts())
