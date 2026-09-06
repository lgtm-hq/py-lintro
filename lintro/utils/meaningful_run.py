"""Decide whether a run measured enough to be a comparable quality signal.

Two consumers ask the same question in slightly different ways, and both got
it wrong independently before this module existed (issue #1739):

* ``lintro badge`` must not publish a public "0 issues" badge for a directory
  nothing inspected, or for a run whose tools timed out.
* The severity baseline must not record — or compare against — a run that did
  not measure the same population as a normal ``check``.

A tool that runs over an empty path still returns ``skipped=False`` and
``success=True`` with a ``"No … files found to check"`` message, which is
indistinguishable from a clean pass unless the output is inspected. That is
what :func:`result_inspected_files` is for.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lintro.enums.action import Action

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult

__all__ = [
    "baseline_is_eligible",
    "result_inspected_files",
    "run_inspected_files",
]

# Real wrapper messages vary: "No files found to check.", "No Astro files to
# check.", "No .py/.pyi files found to check.".
_NO_FILES_CHECKED_RE = re.compile(r"(?i)\bno\b.*\bfiles?\b.*\bto check\b")


def result_inspected_files(result: ToolResult) -> bool:
    """Return whether a tool result looks like it actually inspected files.

    Args:
        result: One tool's completed result.

    Returns:
        bool: ``True`` when the tool was not skipped, did not time out, and
        did not report an empty file set.
    """
    if result.skipped or result.timed_out:
        return False
    text = f"{result.output or ''}\n{result.formatted_output or ''}"
    return _NO_FILES_CHECKED_RE.search(text) is None


def run_inspected_files(results: Sequence[ToolResult]) -> bool:
    """Return whether any tool in a run actually inspected files.

    Args:
        results: Every result the run produced, skipped placeholders included.

    Returns:
        bool: ``True`` when at least one result inspected files.
    """
    return any(result_inspected_files(result) for result in results)


def baseline_is_eligible(
    *,
    action: Action,
    dry_run_preview: bool,
    tool_results: Sequence[ToolResult],
    early_exit: bool = False,
) -> bool:
    """Return whether a run may read or write the severity baseline.

    The same predicate gates both sides on purpose: a run that must not
    *record* a baseline must not *compare against* one either, or it reports a
    delta between two different populations.

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

    Args:
        action: The action the run executed.
        dry_run_preview: Whether this run is a ``fmt --dry-run`` preview.
        tool_results: Every result the run produced.
        early_exit: Whether the run stopped before executing any tool.

    Returns:
        bool: ``True`` when the run is a comparable measurement.
    """
    if early_exit or dry_run_preview or action != Action.CHECK:
        return False
    return run_inspected_files(tool_results)
