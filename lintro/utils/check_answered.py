"""Shared check-verdict interpretation without tool or plugin dependencies."""

from __future__ import annotations

from loguru import logger

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
        Skips, empty scopes, timeouts (even with partial findings), and
        execution failures without findings do not answer the check. Reports
        with parse failures never answer, even when they retain usable findings.
    """
    if result is None or result.parse_failures_count:
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
