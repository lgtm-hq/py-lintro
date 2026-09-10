"""Tests for the golangci-lint plugin definition."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.tools.golangci_lint.definition import (
    GolangciLintPlugin,
    _build_golangci_lint_command,
    _merge_fix_results,
)


def _module_result(
    *,
    success: bool,
    timed_out: bool = False,
    initial: int = 0,
    fixed: int = 0,
    remaining: int = 0,
    output: str | None = None,
) -> ToolResult:
    """Build one per-module fix stage result for the merge helper.

    Args:
        success: Whether the module's fix cycle succeeded.
        timed_out: Whether one of the module's invocations hit its deadline.
        initial: Issues detected before the fix ran.
        fixed: Issues the fix resolved.
        remaining: Issues still present after the fix.
        output: Combined tool output for the module.

    Returns:
        ToolResult shaped like one Go module root's fix outcome.
    """
    return ToolResult(
        name="golangci-lint",
        success=success,
        timed_out=timed_out,
        output=output,
        issues_count=remaining,
        initial_issues_count=initial,
        fixed_issues_count=fixed,
        remaining_issues_count=remaining,
    )


def test_definition_basics() -> None:
    """The definition exposes the expected identity and capabilities."""
    definition = GolangciLintPlugin().definition
    assert_that(definition.name).is_equal_to("golangci_lint")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.file_patterns).contains("*.go")
    assert_that(definition.version_command).is_equal_to(
        ["golangci-lint", "version"],
    )


def test_definition_native_configs() -> None:
    """All golangci-lint config filenames are declared."""
    definition = GolangciLintPlugin().definition
    assert_that(definition.native_configs).contains(
        ".golangci.yml",
        ".golangci.yaml",
        ".golangci.toml",
        ".golangci.json",
    )


def test_doc_url_for_linter() -> None:
    """doc_url() builds a per-linter documentation anchor."""
    plugin = GolangciLintPlugin()
    assert_that(plugin.doc_url("errcheck")).is_equal_to(
        "https://golangci-lint.run/usage/linters/#errcheck",
    )


def test_doc_url_empty_code_returns_none() -> None:
    """doc_url() returns None when no code is supplied."""
    assert_that(GolangciLintPlugin().doc_url("")).is_none()


def test_check_command_allows_parallel_runners() -> None:
    """The run command opts out of golangci-lint's exclusive start-up lock.

    Without ``--allow-parallel-runners`` a second concurrent instance exits 3
    with ``parallel golangci-lint is running`` and an empty ``Issues`` array,
    so findings silently vanish under a parallel test suite (#2391).
    """
    assert_that(_build_golangci_lint_command(fix=False)).contains(
        "--allow-parallel-runners",
    )


def test_fix_command_allows_parallel_runners() -> None:
    """The ``--fix`` command carries the same opt-out as the check command."""
    cmd = _build_golangci_lint_command(fix=True)
    assert_that(cmd).contains("--allow-parallel-runners", "--fix")


def test_merge_fix_results_propagates_timed_out_fix_stage() -> None:
    """A module whose ``--fix`` run timed out marks the aggregate timed out.

    The timeout is an execution failure, so it must survive the merge and
    force the aggregate to fail even though the other module root is clean
    (#2386).
    """
    merged = _merge_fix_results(
        name="golangci-lint",
        results=[
            _module_result(success=True),
            _module_result(
                success=False,
                timed_out=True,
                initial=3,
                remaining=3,
                output="golangci-lint timed out after 30s",
            ),
        ],
    )
    assert_that(merged.timed_out).is_true()
    assert_that(merged.success).is_false()
    assert_that(merged.output).contains("timed out")
    assert_that(merged.remaining_issues_count).is_equal_to(3)


def test_merge_fix_results_propagates_timed_out_check_stage() -> None:
    """A module whose pre-fix check timed out is reported the same way.

    The initial check never completes, so the stage result carries no counts
    at all; only the timeout flag distinguishes it from a clean module.
    """
    merged = _merge_fix_results(
        name="golangci-lint",
        results=[
            _module_result(
                success=False,
                timed_out=True,
                output="golangci-lint timed out after 30s",
            ),
            _module_result(success=True, initial=2, fixed=2),
        ],
    )
    assert_that(merged.timed_out).is_true()
    assert_that(merged.success).is_false()
    assert_that(merged.fixed_issues_count).is_equal_to(2)


def test_merge_fix_results_without_timeout_is_unchanged() -> None:
    """Two clean module roots merge into a successful, non-timed-out result."""
    merged = _merge_fix_results(
        name="golangci-lint",
        results=[
            _module_result(success=True, initial=1, fixed=1),
            _module_result(success=True, initial=4, fixed=4),
        ],
    )
    assert_that(merged.timed_out).is_false()
    assert_that(merged.success).is_true()
    assert_that(merged.initial_issues_count).is_equal_to(5)
    assert_that(merged.output).is_none()


def test_merge_fix_results_failure_without_timeout_is_not_timed_out() -> None:
    """A plain module failure keeps ``timed_out`` false on the aggregate.

    Only a real deadline breach may set the flag; a module that merely still
    has remaining findings must not be mistaken for an infrastructure flake.
    """
    merged = _merge_fix_results(
        name="golangci-lint",
        results=[
            _module_result(success=True, initial=1, fixed=1),
            _module_result(success=False, initial=2, fixed=1, remaining=1),
        ],
    )
    assert_that(merged.timed_out).is_false()
    assert_that(merged.success).is_false()
    assert_that(merged.issues_count).is_equal_to(1)
