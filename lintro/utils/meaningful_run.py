"""Decide whether a run measured enough to be a comparable quality signal.

Two consumers ask the same question in slightly different ways, and both got
it wrong independently before this module existed (issue #1739):

* ``lintro badge`` must not publish a public "0 issues" badge for a directory
  nothing inspected, or for a run whose tools timed out or failed without
  reporting findings.
* The severity baseline must not record — or compare against — a run that did
  not measure the same population as a normal ``check``.

Inspection is a producer-owned, three-state signal: True for inspected files,
False for no work, and None for legacy results whose coverage is unknown.
``no_files`` distinguishes empty discovery from a decline such as missing
configuration. Message text never establishes either state. Only an affirmative
inspection can qualify a run for publication or baseline comparison.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult

__all__ = [
    "baseline_is_eligible",
    "result_inspected_files",
    "run_has_unreported_failure",
    "run_inspected_files",
]


def result_inspected_files(result: ToolResult) -> bool | None:
    """Read a producer's inspection signal without interpreting display text.

    Args:
        result: One tool's completed result.

    Returns:
        True for inspection, False for no work, or None for legacy results.
        Skips, timeouts and empty discovery never qualify as inspections.
    """
    if result.skipped or result.timed_out or result.no_files:
        return False
    return result.inspected_files


def run_inspected_files(results: Sequence[ToolResult]) -> bool:
    """Return whether any tool in a run actually inspected files.

    Args:
        results: Every result the run produced, skipped placeholders included.

    Returns:
        bool: ``True`` when at least one result inspected files.
    """
    return any(result_inspected_files(result) is True for result in results)


def run_has_unreported_failure(results: Sequence[ToolResult]) -> bool:
    """Return whether a tool failed without reporting any findings.

    Args:
        results: Every result the run produced, skipped placeholders included.

    Returns:
        bool: ``True`` when a non-skipped tool failed without parsed findings,
        even if ``issues_count`` is positive (as for run-level gates).
        Failures with parsed findings are ordinary lint verdicts.
    """
    return any(
        not result.skipped and not result.success and not result.issues
        for result in results
    )


def baseline_is_eligible(
    *,
    action: Action,
    dry_run_preview: bool,
    tool_results: Sequence[ToolResult],
    early_exit: bool = False,
    partial_scan: bool = False,
) -> bool:
    """Return whether a run may read or write the severity baseline.

    The same predicate gates both sides on purpose: a run that must not
    *record* a baseline must not *compare against* one either, or it reports a
    delta between two different populations.

    Baseline storage additionally uses a matching scope fingerprint for
    comparison and updates only that scope's entry.

    A run qualifies only when all of the following hold:

    * It is a ``check``. ``fmt`` and ``test`` measure something else.
    * It is not a ``fmt --dry-run`` preview. Those report ``CHECK`` here
      because they execute read-only, but their results are filtered to the
      auto-fixable subset, so their counts are a strict undercount.
    * At least one tool actually inspected files. An all-skipped run still
      carries ``skipped=True`` placeholder results, so a non-empty result list
      is not evidence that anything was measured; recording its zero counts
      would make the next real check report every existing issue as newly
      introduced.
    * It did not exit before running anything.
    * It did not request ``--diff`` or ``--incremental``. These modes narrow
      the measured files without changing the path arguments or tool set,
      and their file population can change even with the same flags.
    * No tool failed without parsed findings. Such a failure would undercount
      the population and make the next healthy run report spurious new issues.

    Args:
        action: The action the run executed.
        dry_run_preview: Whether this run is a ``fmt --dry-run`` preview.
        tool_results: Every result the run produced.
        early_exit: Whether the run stopped before executing any tool.
        partial_scan: Whether ``--diff`` or ``--incremental`` was requested.

    Returns:
        bool: ``True`` when the run is a comparable measurement.
    """
    if early_exit or dry_run_preview or partial_scan or action != Action.CHECK:
        return False
    return not run_has_unreported_failure(tool_results) and run_inspected_files(
        tool_results,
    )
