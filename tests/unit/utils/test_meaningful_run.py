"""Tests for the shared "did this run actually measure anything" predicate."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.meaningful_run import (
    baseline_is_eligible,
    result_inspected_files,
    run_inspected_files,
)


def _real_result() -> ToolResult:
    """Build a result from a tool that genuinely inspected files.

    Returns:
        ToolResult: A completed, non-skipped result with ordinary output.
    """
    return ToolResult(
        name="ruff",
        success=True,
        skipped=False,
        output="All checks passed",
    )


@pytest.mark.parametrize(
    "output",
    [
        "No files found to check.",
        "No Astro files to check.",
        "No .py/.pyi files found to check.",
        "no FILES found TO CHECK",
    ],
    ids=["generic", "astro", "python", "mixed-case"],
)
def test_result_inspected_files_rejects_empty_file_sets(output: str) -> None:
    """A tool that matched nothing is not evidence of a measurement.

    Such a tool still returns ``skipped=False`` and ``success=True``, so the
    message is the only signal that nothing was looked at.

    Args:
        output: A real "nothing to check" message from a wrapped tool.
    """
    result = ToolResult(name="ruff", success=True, skipped=False, output=output)

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_rejects_a_skipped_tool() -> None:
    """A skipped tool measured nothing."""
    result = ToolResult(
        name="hadolint",
        success=True,
        skipped=True,
        skip_reason="hadolint not found",
    )

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_rejects_a_timed_out_tool() -> None:
    """A timed-out tool's findings were never collected."""
    result = ToolResult(
        name="semgrep",
        success=False,
        skipped=False,
        timed_out=True,
        output="timed out after 300s",
    )

    assert_that(result_inspected_files(result)).is_false()


def test_result_inspected_files_accepts_a_real_run() -> None:
    """An ordinary completed tool counts as a measurement."""
    assert_that(result_inspected_files(_real_result())).is_true()


def test_run_inspected_files_needs_only_one_real_tool() -> None:
    """One real tool is enough, even beside skipped placeholders."""
    results = [
        ToolResult(
            name="hadolint",
            success=True,
            skipped=True,
            skip_reason="hadolint not found",
        ),
        _real_result(),
    ]

    assert_that(run_inspected_files(results)).is_true()


def test_run_inspected_files_rejects_an_all_skipped_run() -> None:
    """Skipped placeholders are results, but they are not measurements."""
    results = [
        ToolResult(
            name="hadolint",
            success=True,
            skipped=True,
            skip_reason="hadolint not found",
        ),
    ]

    assert_that(run_inspected_files(results)).is_false()


def test_baseline_is_eligible_for_a_real_check() -> None:
    """A plain check that inspected files is a comparable measurement."""
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[_real_result()],
        ),
    ).is_true()


@pytest.mark.parametrize(
    ("action", "dry_run_preview", "results", "early_exit"),
    [
        (Action.FIX, False, [_real_result()], False),
        (Action.TEST, False, [_real_result()], False),
        (Action.CHECK, True, [_real_result()], False),
        (Action.CHECK, False, [], False),
        (Action.CHECK, False, [_real_result()], True),
        (
            Action.CHECK,
            False,
            [
                ToolResult(
                    name="hadolint",
                    success=True,
                    skipped=True,
                    skip_reason="hadolint not found",
                ),
            ],
            False,
        ),
    ],
    ids=[
        "fmt",
        "test",
        "dry-run-preview",
        "empty",
        "early-exit",
        "all-skipped",
    ],
)
def test_baseline_is_not_eligible_for_an_unmeasured_run(
    action: Action,
    dry_run_preview: bool,
    results: list[ToolResult],
    early_exit: bool,
) -> None:
    """Every shape that measured a different population is refused.

    Args:
        action: The action the run executed.
        dry_run_preview: Whether this is a ``fmt --dry-run`` preview.
        results: The run's tool results.
        early_exit: Whether the run stopped before executing any tool.
    """
    assert_that(
        baseline_is_eligible(
            action=action,
            dry_run_preview=dry_run_preview,
            tool_results=results,
            early_exit=early_exit,
        ),
    ).is_false()
