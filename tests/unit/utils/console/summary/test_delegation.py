"""Unit tests for the ThreadSafeConsoleLogger summary rendering methods.

Each test drives the real rendering path and asserts on what a user actually
sees: the text captured from stdout/stderr, or the tracked buffer that backs
``console.log`` and ``report.md``. Nothing here asserts that one function
called another (#2315).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.console.logger import ThreadSafeConsoleLogger
from tests.unit.utils.console.conftest import patch_tty_streams

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.unit.utils.conftest import FakeToolResult


# =============================================================================
# Summary Table Tests
# =============================================================================


def test_print_summary_table_renders_a_row_per_tool(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary table renders the tool name, status and issue count.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=False, issues_count=3)]

    logger._print_summary_table(Action.CHECK, results)

    out = capsys.readouterr().out
    assert_that(out).contains("Tool")
    assert_that(out).contains("test-tool")
    assert_that(out).contains("3")


def test_print_summary_table_accepts_a_string_action(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A string action renders the same table as the matching enum value.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_summary_table(Action.CHECK, [fake_tool_result_factory()])
    from_enum = capsys.readouterr().out

    logger._print_summary_table("check", [fake_tool_result_factory()])
    from_string = capsys.readouterr().out

    assert_that(from_string).is_equal_to(from_enum)


@pytest.mark.parametrize(
    ("action_str", "expected_action"),
    [
        ("check", Action.CHECK),
        ("fix", Action.FIX),
        ("fmt", Action.FIX),
        ("test", Action.TEST),
    ],
)
def test_print_summary_table_action_normalization(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    action_str: str,
    expected_action: Action,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every accepted action spelling renders its normalized enum's table.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        action_str: String representation of the action.
        expected_action: Expected Action enum value.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_summary_table(expected_action, [fake_tool_result_factory()])
    from_enum = capsys.readouterr().out

    logger._print_summary_table(action_str, [fake_tool_result_factory()])
    from_string = capsys.readouterr().out

    assert_that(from_string).is_equal_to(from_enum)


# =============================================================================
# Totals Table Tests
# =============================================================================


def test_print_totals_table_renders_check_mode_metrics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CHECK mode renders the issue, severity, fixable and file counts.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_totals_table(
        action=Action.CHECK,
        total_issues=5,
        affected_files=2,
    )

    out = capsys.readouterr().out
    assert_that(out).contains("TOTALS")
    assert_that(out).contains("Total Issues")
    assert_that(out).contains("Affected Files")
    assert_that(out).does_not_contain("Remaining Issues")


def test_print_totals_table_renders_fix_mode_metrics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FIX mode renders fixed, AI and remaining counts instead of issues.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_totals_table(
        action=Action.FIX,
        total_fixed=10,
        total_remaining=3,
        affected_files=5,
        total_ai_applied=2,
        total_ai_verified=1,
    )

    out = capsys.readouterr().out
    assert_that(out).contains("Fixed Issues (Native)")
    assert_that(out).contains("AI Applied Fixes")
    assert_that(out).contains("AI Resolved Fixes")
    assert_that(out).contains("Total Resolved")
    assert_that(out).contains("Remaining Issues")


@pytest.mark.parametrize(
    ("action", "kwargs", "expected_metric"),
    [
        (Action.CHECK, {"total_issues": 0, "affected_files": 0}, "Total Issues"),
        (Action.CHECK, {"total_issues": 10, "affected_files": 5}, "Total Issues"),
        (
            Action.FIX,
            {"total_fixed": 5, "total_remaining": 2, "affected_files": 3},
            "Remaining Issues",
        ),
        (Action.TEST, {"total_issues": 4, "affected_files": 1}, "Total Issues"),
    ],
)
def test_print_totals_table_various_inputs(
    action: Action,
    kwargs: dict[str, int],
    expected_metric: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every action and count combination renders its own metric rows.

    Args:
        action: Action type to test.
        kwargs: Keyword arguments to pass to the method.
        expected_metric: Metric row the rendered table must carry.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_totals_table(action=action, **kwargs)

    out = capsys.readouterr().out
    assert_that(out).contains("TOTALS")
    assert_that(out).contains(expected_metric)


# =============================================================================
# Final Status Tests
# =============================================================================


def test_print_final_status_reports_the_check_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CHECK run with issues reports the count as a failure.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_final_status(Action.CHECK, 5)

    assert_that(capsys.readouterr().out).contains("Found 5 issues.")


def test_print_final_status_accepts_a_string_action(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``fmt`` alias produces the FIX wording, not the CHECK wording.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_final_status("fmt", 3)

    out = capsys.readouterr().out
    assert_that(out).contains("Fixed 3 issues.")
    assert_that(out).does_not_contain("Found")


@pytest.mark.parametrize(
    ("action", "total_issues", "expected_text"),
    [
        (Action.CHECK, 0, "No issues found."),
        (Action.CHECK, 10, "Found 10 issues."),
        (Action.FIX, 0, "No issues found."),
        (Action.FIX, 5, "Fixed 5 issues."),
        ("check", 3, "Found 3 issues."),
        ("fmt", 7, "Fixed 7 issues."),
    ],
)
def test_print_final_status_various_inputs(
    action: Action | str,
    total_issues: int,
    expected_text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each action and issue count combination prints its own wording.

    Args:
        action: Action type (enum or string).
        total_issues: Number of total issues.
        expected_text: Status line the run must print.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_final_status(action, total_issues)

    assert_that(capsys.readouterr().out).contains(expected_text)


# =============================================================================
# Final Status Format Tests
# =============================================================================


def test_print_final_status_format_reports_fixed_and_remaining(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A format run reports the fixed and remaining counts on separate lines.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_final_status_format(10, 2)

    out = capsys.readouterr().out
    assert_that(out).contains("10 fixed")
    assert_that(out).contains("2 remaining")


@pytest.mark.parametrize(
    ("total_fixed", "total_remaining", "expected_text"),
    [
        (0, 0, "No issues found."),
        (5, 0, "5 fixed"),
        (0, 3, "3 remaining"),
        (10, 5, "5 remaining"),
        (100, 50, "100 fixed"),
    ],
)
def test_print_final_status_format_various_counts(
    total_fixed: int,
    total_remaining: int,
    expected_text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each fixed/remaining combination prints the matching status line.

    Args:
        total_fixed: Number of fixed issues.
        total_remaining: Number of remaining issues.
        expected_text: Status line the run must print.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_final_status_format(total_fixed, total_remaining)

    assert_that(capsys.readouterr().out).contains(expected_text)


# =============================================================================
# ASCII Art Tests
# =============================================================================


@pytest.mark.usefixtures("labelled_ascii_art")
def test_print_ascii_art_reaches_the_terminal_untracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Art reaches an interactive stdout but never the captured buffer.

    The buffer backs ``report.md`` and ``console.log``, so the braille blob
    must stay out of it.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger()

    logger._print_ascii_art(5)

    assert_that(stdout.getvalue()).contains("ART:fail.txt")
    assert_that(logger.get_buffer()).is_empty()


@pytest.mark.usefixtures("labelled_ascii_art")
@pytest.mark.parametrize(
    ("issue_count", "expected_art"),
    [
        (0, "ART:success.txt"),
        (1, "ART:fail.txt"),
        (5, "ART:fail.txt"),
        (100, "ART:fail.txt"),
    ],
)
def test_print_ascii_art_selects_art_by_issue_count(
    issue_count: int,
    expected_art: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean run gets the success art; any issue count gets the failure art.

    Args:
        issue_count: Number of issues to display.
        expected_art: Art asset the count must select.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger()

    logger._print_ascii_art(issue_count)

    assert_that(stdout.getvalue()).contains(expected_art)


@pytest.mark.usefixtures("labelled_ascii_art")
def test_print_ascii_art_emits_nothing_when_art_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``art_enabled=False`` (``output.art: false`` / ``--no-art``) prints no art.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger(art_enabled=False)

    logger._print_ascii_art(0)

    assert_that(stdout.getvalue()).is_empty()


@pytest.mark.usefixtures("labelled_ascii_art")
def test_print_ascii_art_goes_to_stderr_when_output_is_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routed output keeps stdout clean by sending art to stderr instead.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, stderr = patch_tty_streams(monkeypatch=monkeypatch)
    logger = ThreadSafeConsoleLogger(route_stderr=True)

    logger._print_ascii_art(0)

    assert_that(stderr.getvalue()).contains("ART:success.txt")
    assert_that(stdout.getvalue()).is_empty()


@pytest.mark.usefixtures("labelled_ascii_art")
def test_print_ascii_art_is_suppressed_on_a_non_tty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Piped or captured output gets no art, keeping documents parseable.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger._print_ascii_art(0)

    assert_that(capsys.readouterr().out).is_empty()


# =============================================================================
# Integration Tests - Full Execution Summary Flow
# =============================================================================


def test_execution_summary_outputs_header_and_border(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The execution summary opens with its styled header and border.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=0)]

    logger.print_execution_summary(Action.CHECK, results)

    out = capsys.readouterr().out
    assert_that(out).contains("EXECUTION SUMMARY")
    assert_that(out).contains("=" * 50)


def test_execution_summary_renders_both_tables(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary carries the per-tool table and the totals table together.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=3)]

    logger.print_execution_summary(Action.CHECK, results)

    out = capsys.readouterr().out
    assert_that(out).contains("test-tool")
    assert_that(out).contains("TOTALS")
    assert_that(out).contains("Total Issues")


def test_execution_summary_empty_results_report_zero_totals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No tool results still renders a complete summary with zero totals.

    Args:
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()

    logger.print_execution_summary(Action.CHECK, [])

    out = capsys.readouterr().out
    assert_that(out).contains("EXECUTION SUMMARY")
    assert_that(out).contains("Total Issues")
    assert_that(out).contains("Affected Files")


@pytest.mark.parametrize(
    "action",
    [Action.CHECK, Action.FIX, Action.TEST],
)
def test_execution_summary_all_action_types(
    fake_tool_result_factory: Callable[..., FakeToolResult],
    action: Action,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CHECK, FIX and TEST all produce a complete summary without errors.

    Args:
        fake_tool_result_factory: Factory for creating FakeToolResult instances.
        action: Action type to test.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger()
    results = [fake_tool_result_factory(success=True, issues_count=0)]

    logger.print_execution_summary(action, results)

    out = capsys.readouterr().out
    assert_that(out).contains("EXECUTION SUMMARY")
    assert_that(out).contains("TOTALS")
