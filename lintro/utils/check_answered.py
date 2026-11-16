"""Shared check-verdict interpretation without tool or plugin dependencies."""

from __future__ import annotations

from loguru import logger

from lintro.models.core.result_completeness import is_incomplete_result
from lintro.models.core.tool_outcome import (
    Checked,
    NoFiles,
    Skipped,
    TimedOut,
    Unavailable,
)
from lintro.models.core.tool_result import ToolResult


def check_answered(result: ToolResult | None) -> bool:
    """Return whether a check measured its scope, including remaining findings.

    Args:
        result: The check result, or None when the tool could not run.

    Returns:
        True for a completed clean check or one reporting real findings.
        Skips, empty scopes and failures without findings do not answer the
        check. Neither does an incomplete result (#3498): a timeout, a run
        marked ``execution_failed`` (for example a refused command), an
        unknown residual, or a report with parse failures never answers,
        even when it retains usable findings. Its findings are partial, so
        they can neither confirm nor reject a fix.
    """
    if result is None or is_incomplete_result(result):
        return False
    try:
        execution = result.outcome.execution
    except ValueError:
        # Legacy plugins can return contradictory flags or counts. They do
        # not establish a verdict; the strict outcome adapter still rejects
        # them when read directly.
        logger.opt(exception=True).debug("Invalid check result cannot verify fixes")
        return False
    match execution:
        case Checked(success=success):
            return success or bool(result.issues)
        case Skipped() | Unavailable() | NoFiles() | TimedOut():
            return False
