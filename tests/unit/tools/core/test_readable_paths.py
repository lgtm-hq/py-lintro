"""Paths a tool would misread never reach it (#3393)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import (
    Checked,
    FixMeasured,
    NoFiles,
    Skipped,
    ToolOutcome,
)
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import PreparedExecution
from lintro.tools.core.readable_paths import run_on_readable_paths


def _ctx(rel_files: list[str], *, relative: bool = True) -> PreparedExecution:
    """Build a prepared context for the given relative paths.

    Args:
        rel_files: Relative paths.
        relative: Whether relative paths were computed at all.

    Returns:
        The context.
    """
    return PreparedExecution(
        files=[f"/repo/{name}" for name in rel_files],
        rel_files=rel_files if relative else [],
        cwd="/repo",
        timeout=30,
    )


def _drive(
    ctx: PreparedExecution,
    result: ToolResult,
) -> tuple[ToolResult, list[PreparedExecution]]:
    """Run the helper with a tool that returns ``result``.

    Args:
        ctx: The prepared context.
        result: What the tool run returns.

    Returns:
        The final result and every context the tool ran on.
    """
    seen: list[PreparedExecution] = []

    def run(readable: PreparedExecution) -> ToolResult:
        seen.append(readable)
        return result

    final = run_on_readable_paths(
        ctx,
        readable=lambda path: "!" not in path,
        reason=lambda paths: f"withheld {paths}",
        run=run,
        unrun=lambda reason, cwd: ToolResult.from_outcome(
            ToolOutcome(execution=Checked(success=False, inspected_files=None)),
            name="tool",
            output=f"unrun: {reason} in {cwd}",
            execution_failed=True,
            findings_complete=False,
        ),
    )
    return final, seen


CLEAN = ToolResult.from_outcome(
    ToolOutcome(execution=Checked(success=True, inspected_files=True)),
    name="tool",
    output="All issues were successfully auto-fixed",
)


@pytest.mark.parametrize("relative", [True, False], ids=["rel_files", "files"])
def test_unreadable_paths_are_dropped_and_the_run_is_incomplete(
    relative: bool,
) -> None:
    """Only readable paths run; a clean run over them still fails.

    Args:
        relative: Whether the context carries relative paths.
    """
    result, seen = _drive(_ctx(["ok.css", "bad!.css"], relative=relative), CLEAN)

    assert_that(seen).is_length(1)
    assert_that(seen[0].files).is_equal_to(["/repo/ok.css"])
    assert_that(seen[0].rel_files).is_equal_to(["ok.css"] if relative else [])
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.output).starts_with("withheld [").contains(
        "successfully auto-fixed",
    )


def test_fix_accounting_for_the_readable_paths_is_kept() -> None:
    """Counts for the files that were fixed survive the incomplete verdict."""
    fixed = ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(success=True, inspected_files=True),
            accounting=FixMeasured(initial=1, fixed=1, remaining=0),
        ),
        name="tool",
    )
    result, _ = _drive(_ctx(["ok.css", "bad!.css"]), fixed)

    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_zero()


def test_no_readable_path_never_runs_the_tool() -> None:
    """With nothing readable the tool is not run at all."""
    result, seen = _drive(_ctx(["bad!.css"]), CLEAN)

    assert_that(seen).is_empty()
    assert_that(result.output).is_equal_to("unrun: withheld ['bad!.css'] in /repo")


def test_readable_rest_ignored_by_the_tool_is_still_a_failure() -> None:
    """A no-files run over the rest cannot hide the withheld paths."""
    no_files = ToolResult.from_outcome(
        ToolOutcome(execution=NoFiles()),
        name="tool",
        output="ignored",
    )
    result, seen = _drive(_ctx(["ok.css", "bad!.css"]), no_files)

    assert_that(seen).is_length(1)
    assert_that(result.no_files).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).starts_with("unrun: ")


def test_all_readable_runs_unchanged_and_skip_passes_through() -> None:
    """Readable paths run as given, and a skip over the rest is kept as is."""
    skipped = ToolResult.from_outcome(
        ToolOutcome(execution=Skipped("no config")),
        name="tool",
        output="skip",
    )
    unchanged, seen = _drive(_ctx(["ok.css"]), CLEAN)
    passed_on, _ = _drive(_ctx(["ok.css", "bad!.css"]), skipped)

    assert_that(seen[0].rel_files).is_equal_to(["ok.css"])
    assert_that(unchanged).is_same_as(CLEAN)
    assert_that(passed_on).is_same_as(skipped)
