"""Tests for shared install result rendering."""

from __future__ import annotations

from assertpy import assert_that
from rich.console import Console

from lintro.cli_utils.install_output import (
    count_outcomes,
    render_install_results,
    render_outcome_summary,
    unresolved_tool_names,
)
from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_plan import InstallResult
from lintro.tools.core.tool_registry import ManifestTool


def _result(
    name: str,
    outcome: InstallOutcome,
    *,
    step: int,
    total: int,
) -> InstallResult:
    """Build an InstallResult for rendering tests.

    Args:
        name: Tool name.
        outcome: Outcome to report.
        step: 1-based position in the batch.
        total: Batch size.

    Returns:
        InstallResult instance.
    """
    return InstallResult(
        tool=ManifestTool(
            name=name,
            version="1.0.0",
            min_version="1.0.0",
            install_type="binary",
        ),
        outcome=outcome,
        message=f"{name} message",
        duration_seconds=1.0,
        command=f"brew install {name}",
        step=step,
        total_steps=total,
    )


def _render(results: list[InstallResult]) -> str:
    """Render results to a plain string.

    Args:
        results: Results to render.

    Returns:
        Captured console output.
    """
    console = Console(width=200, force_terminal=False, no_color=True)
    with console.capture() as capture:
        render_install_results(console, results)
        render_outcome_summary(console, results)
    return capture.get()


def test_render_shows_every_step_after_an_early_failure() -> None:
    """Later actions are visibly reported after an earlier failure."""
    output = _render(
        [
            _result("golangci_lint", InstallOutcome.FAILED, step=1, total=3),
            _result("clippy", InstallOutcome.TIMED_OUT, step=2, total=3),
            _result("ruff", InstallOutcome.SUCCESS, step=3, total=3),
        ],
    )

    assert_that(output).contains("[1/3] FAIL  golangci_lint")
    assert_that(output).contains("[2/3] TIMEOUT  clippy")
    assert_that(output).contains("[3/3] OK  ruff")
    assert_that(output).contains("Attempted 3 action(s)")


def test_render_distinguishes_not_discoverable_from_success() -> None:
    """A succeeded-but-undiscoverable install is not reported as OK."""
    output = _render(
        [_result("vale", InstallOutcome.NOT_DISCOVERABLE, step=1, total=1)],
    )

    assert_that(output).contains("PATH  vale")
    assert_that(output).contains("installed but not on PATH")


def test_count_outcomes_covers_every_outcome() -> None:
    """Counting yields an entry for each outcome, defaulting to zero."""
    counts = count_outcomes(
        [_result("ruff", InstallOutcome.SUCCESS, step=1, total=1)],
    )

    assert_that(counts).is_length(len(InstallOutcome))
    assert_that(counts[InstallOutcome.SUCCESS]).is_equal_to(1)
    assert_that(counts[InstallOutcome.FAILED]).is_equal_to(0)


def test_unresolved_names_exclude_retryable_outcomes() -> None:
    """Only a timeout is worth retrying unchanged; other issues are not."""
    unresolved = unresolved_tool_names(
        [
            _result("golangci_lint", InstallOutcome.FAILED, step=1, total=5),
            _result("clippy", InstallOutcome.TIMED_OUT, step=2, total=5),
            _result("vale", InstallOutcome.MANUAL_BLOCKED, step=3, total=5),
            _result("taplo", InstallOutcome.NOT_DISCOVERABLE, step=4, total=5),
            _result("ruff", InstallOutcome.SUCCESS, step=5, total=5),
        ],
    )

    assert_that(unresolved).is_equal_to(["golangci_lint", "vale", "taplo"])
