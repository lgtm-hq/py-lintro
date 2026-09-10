"""Unit tests for ThreadSafeConsoleLogger execution summary methods.

This module tests the execution summary functionality of ThreadSafeConsoleLogger,
including CHECK and FIX action handling. Counts are asserted by parsing the
rendered TOTALS table back out of the captured output rather than by inspecting
mock calls (#2315).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from tests.unit.utils.console.conftest import patch_tty_streams

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.unit.utils.conftest import FakeToolResult

# Braille block used by the decorative ASCII art (U+2800..U+28FF).
_BRAILLE_RE = re.compile(r"[\u2800-\u28ff]")

# One rendered totals row, e.g. "| Total Issues   | 8       |".
_TOTALS_ROW_RE = re.compile(r"^\|\s*(?P<metric>[^|]+?)\s*\|\s*(?P<count>\d+)\s*\|$")


def _totals_row(*, output: str) -> dict[str, int]:
    """Parse the rendered TOTALS table back into metric/count pairs.

    Args:
        output: Captured console output containing a totals table.

    Returns:
        A mapping of metric label to the count rendered beside it.
    """
    return {
        match.group("metric"): int(match.group("count"))
        for line in output.splitlines()
        if (match := _TOTALS_ROW_RE.match(line.strip()))
    }


# =============================================================================
# Execution Summary Tests - CHECK Action
# =============================================================================


@pytest.mark.usefixtures("labelled_ascii_art")
def test_execution_summary_check_no_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean CHECK run reports zero issues and shows the success art.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=0)]

    logger.print_execution_summary(Action.CHECK, results)

    out = stdout.getvalue()
    assert_that(out).contains("Total Issues")
    assert_that(out).contains("ART:success.txt")


@pytest.mark.usefixtures("labelled_ascii_art")
def test_execution_summary_check_with_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The aggregated total reaches both the totals table and the art choice.

    Two tools reporting 5 and 3 issues must render a summed total of 8, and
    the same non-zero total must select the failure art rather than the
    success art.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(success=True, issues_count=5),
        fake_tool_result_factory(success=True, issues_count=3),
    ]

    logger.print_execution_summary(Action.CHECK, results)

    out = stdout.getvalue()
    assert_that(_totals_row(output=out)).contains_entry({"Total Issues": 8})
    assert_that(out).contains("ART:fail.txt")
    assert_that(out).does_not_contain("ART:success.txt")


@pytest.mark.usefixtures("labelled_ascii_art")
def test_execution_summary_check_failed_tool_shows_minimum_issues(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed tool reporting zero issues still shows the failure art.

    The counted total stays at zero, but the run must not look like a clean
    pass, so the art is selected as if there were at least one issue.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=False, issues_count=0)]

    logger.print_execution_summary(Action.CHECK, results)

    out = stdout.getvalue()
    assert_that(out).contains("ART:fail.txt")
    assert_that(out).does_not_contain("ART:success.txt")


@pytest.mark.parametrize(
    ("issue_counts", "expected_total"),
    [
        ([0], 0),
        ([5], 5),
        ([5, 3], 8),
        ([1, 2, 3, 4], 10),
        ([0, 0, 0], 0),
    ],
)
def test_execution_summary_check_issue_aggregation(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    issue_counts: list[int],
    expected_total: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The totals table reports the sum of every tool's issue count.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        issue_counts: List of issue counts for each tool.
        expected_total: Expected total issues after aggregation.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(success=True, issues_count=count)
        for count in issue_counts
    ]

    logger.print_execution_summary(Action.CHECK, results)

    assert_that(_totals_row(output=capsys.readouterr().out)).contains_entry(
        {"Total Issues": expected_total},
    )


# =============================================================================
# Execution Summary Tests - FIX Action
# =============================================================================


def test_execution_summary_fix_with_standardized_counts(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Standardized fix counts are reported verbatim, not re-parsed from output.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            fixed_issues_count=10,
            remaining_issues_count=2,
        ),
    ]

    logger.print_execution_summary(Action.FIX, results)

    rows = _totals_row(output=capsys.readouterr().out)
    assert_that(rows).contains_entry({"Fixed Issues (Native)": 10})
    assert_that(rows).contains_entry({"Remaining Issues": 2})


def test_execution_summary_fix_fallback_to_issues_count(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary falls back when fixed_issues_count not provided.

    Legacy tools that don't provide fixed_issues_count should still have
    their issues_count used for the summary calculation.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            issues_count=5,
            fixed_issues_count=None,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise any exception
        logger.print_execution_summary(Action.FIX, results)


def test_execution_summary_fix_failed_tool_handled(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary handles failed tools in fix action gracefully.

    Failed tools should not contribute to numeric totals to avoid misleading
    success metrics.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=False,
            issues_count=0,
            remaining_issues_count=None,
        ),
    ]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise and should handle sentinel values
        logger.print_execution_summary(Action.FIX, results)


def test_execution_summary_fix_parses_remaining_from_output(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no standardized count, the remaining total is read from the output.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            output="5 remaining issues that cannot be auto-fixed",
            remaining_issues_count=None,
        ),
    ]

    logger.print_execution_summary(Action.FIX, results)

    assert_that(_totals_row(output=capsys.readouterr().out)).contains_entry(
        {"Remaining Issues": 5},
    )


def test_execution_summary_fix_parses_cannot_autofix_from_output(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The "cannot be auto-fixed" wording also yields the remaining total.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            output="Found 3 issues that cannot be auto-fixed",
            remaining_issues_count=None,
        ),
    ]

    logger.print_execution_summary(Action.FIX, results)

    assert_that(_totals_row(output=capsys.readouterr().out)).contains_entry(
        {"Remaining Issues": 3},
    )


def test_execution_summary_fix_handles_string_sentinel_remaining(
    fake_tool_result_factory: Callable[..., FakeToolResult],
) -> None:
    """Verify print_execution_summary handles string sentinels.

    String sentinel values (like 'N/A') should not be added to numeric
    totals to prevent type errors in calculations.


    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
    """
    logger = ThreadSafeConsoleLogger()
    result = fake_tool_result_factory(success=True)
    # Set a string sentinel using object attribute
    result.remaining_issues_count = "N/A"  # type: ignore[assignment]
    results = [result]

    with (
        patch.object(logger, "console_output"),
        patch.object(logger, "_print_summary_table"),
        patch.object(logger, "_print_ascii_art"),
    ):
        # Should not raise or add string sentinel to numeric total
        logger.print_execution_summary(Action.FIX, results)


# =============================================================================
# ASCII Art Gating - Buffer / TTY / Toggle
# =============================================================================


def test_art_never_enters_tracked_buffer_on_tty(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify braille art reaches the TTY but never the captured buffer.

    ``report.md`` and ``console.log`` are rendered from ``get_buffer()``. Art
    must be emitted via the untracked writer so those machine-facing artifacts
    stay free of the non-ASCII braille blob, even during an interactive run.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest capture fixture for stdout/stderr.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=False, issues_count=3)]

    with patch(
        "lintro.utils.display_helpers.sys.stdout.isatty",
        return_value=True,
    ):
        logger.print_execution_summary(Action.CHECK, results)

    captured = capsys.readouterr()
    # Art is emitted to the terminal on a TTY...
    assert_that(bool(_BRAILLE_RE.search(captured.out))).is_true()
    # ...but must not leak into the tracked buffer backing report.md.
    assert_that(bool(_BRAILLE_RE.search(logger.get_buffer()))).is_false()


def test_art_suppressed_in_buffer_and_stdout_when_not_tty(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify no braille art on stdout or in the buffer when not a TTY.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest capture fixture for stdout/stderr.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=False, issues_count=3)]

    with patch(
        "lintro.utils.display_helpers.sys.stdout.isatty",
        return_value=False,
    ):
        logger.print_execution_summary(Action.CHECK, results)

    captured = capsys.readouterr()
    assert_that(bool(_BRAILLE_RE.search(captured.out))).is_false()
    assert_that(bool(_BRAILLE_RE.search(logger.get_buffer()))).is_false()


def test_art_suppressed_when_art_disabled_even_on_tty(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify ``art_enabled=False`` suppresses art on an interactive TTY.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest capture fixture for stdout/stderr.
    """
    logger = ThreadSafeConsoleLogger(art_enabled=False)
    results = [fake_tool_result_factory(success=False, issues_count=3)]

    with patch(
        "lintro.utils.display_helpers.sys.stdout.isatty",
        return_value=True,
    ):
        logger.print_execution_summary(Action.CHECK, results)

    captured = capsys.readouterr()
    assert_that(bool(_BRAILLE_RE.search(captured.out))).is_false()
    assert_that(bool(_BRAILLE_RE.search(logger.get_buffer()))).is_false()


@pytest.mark.parametrize(
    ("fixed", "remaining", "expected_remaining"),
    [
        (10, 0, 0),
        (5, 3, 3),
        (0, 0, 0),
        (100, 10, 10),
    ],
)
def test_execution_summary_fix_various_counts(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    fixed: int,
    remaining: int,
    expected_remaining: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each fixed/remaining combination reaches the totals table intact.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        fixed: Number of fixed issues.
        remaining: Number of remaining issues.
        expected_remaining: Expected remaining issues total.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [
        fake_tool_result_factory(
            success=True,
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
        ),
    ]

    logger.print_execution_summary(Action.FIX, results)

    rows = _totals_row(output=capsys.readouterr().out)
    assert_that(rows).contains_entry({"Fixed Issues (Native)": fixed})
    assert_that(rows).contains_entry({"Remaining Issues": expected_remaining})
