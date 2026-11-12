"""Whether a fix result is incomplete, shared by retry and verification (#3352).

The fix convergence retry and the run-level verify fold must agree on what
counts as an incomplete stage: one that failed to execute, timed out, could
not measure its residual, or printed a report that could not be fully parsed.
Such a stage's counts stay unknown, and the findings it did report are kept
beside the ones detected before it.
"""

from __future__ import annotations

from lintro.models.core.tool_result import ToolResult


def is_incomplete_result(result: ToolResult) -> bool:
    """Report whether a fix stage left evidence nothing later may overwrite.

    Args:
        result: One fix or verification stage.

    Returns:
        True when the stage failed to execute, timed out, has unknown fix
        accounting, or printed a report that could not be fully parsed.
    """
    return bool(
        result.execution_failed
        or result.timed_out
        or result.residual_unknown
        or result.parse_failures_count,
    )


__all__ = ["is_incomplete_result"]
