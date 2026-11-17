"""Name why a tool result is no complete verdict, one shape at a time (#3497).

The AI review lint digest and the advisory runner both report a tool that
did not run or did not complete instead of reading its silence as clean.
They share this module so a result gets the same verdict, and the same
reason, on every surface. It lives in :mod:`lintro.utils` so the advisory
runner, which is in the ``utils`` layer, can use it.

Each incomplete shape gets its own reason rather than the first line of the
tool's output, which for a partial parse is often one of its findings:

- timed out: ``timed out``;
- an execution failure (a refused command, an npx integrity refusal, a
  crash): the first line of the output, which those producers lead with;
- a report that could not be fully parsed: how much of it could not be;
- an unknown residual: ``remaining issues unknown`` and why;
- a failure with no findings to explain it: ``failed with no findings``.

A skipped or unavailable tool is not incomplete: it made no claim either way.
"""

from __future__ import annotations

from lintro.models.core.tool_result import ToolResult
from lintro.utils.quoting import quote_first_line

__all__ = ["incomplete_reason", "unavailable_reason"]

#: Longest piece of tool output quoted in a reason.
_QUOTE_LIMIT: int = 160


def incomplete_reason(result: ToolResult) -> str | None:
    """Name why *result* is no complete verdict, or None when it is one.

    ``findings_complete`` alone is not consulted: legacy plugins never set
    it, so it would call every one of them incomplete.

    Args:
        result: One tool's result.

    Returns:
        A one-line reason cleaned of terminal control sequences, or None for
        a complete, skipped or unavailable result.
    """
    if result.skipped or result.unavailable:
        return None
    if result.timed_out:
        return "timed out"
    if result.execution_failed:
        line = quote_first_line(result.output, limit=_QUOTE_LIMIT)
        return line or "execution failed"
    if result.parse_failures_count:
        return (
            f"{result.parse_failures_count} part(s) of {result.name}'s output "
            "could not be parsed; its findings are partial"
        )
    if result.residual_unknown:
        why = quote_first_line(result.residual_unknown_reason, limit=_QUOTE_LIMIT)
        return _with_detail("remaining issues unknown", why)
    if not result.success and not result.issues and not result.issues_count:
        line = quote_first_line(result.output, limit=_QUOTE_LIMIT)
        return _with_detail("failed with no findings", line)
    return None


def _with_detail(reason: str, detail: str) -> str:
    """Append a quoted detail to a reason in parentheses, when there is one.

    Args:
        reason: The fixed reason.
        detail: The cleaned detail, possibly empty.

    Returns:
        ``"reason (detail)"``, or the reason alone when the detail is empty
        or only repeats it.
    """
    return f"{reason} ({detail})" if detail and detail != reason else reason


def unavailable_reason(result: ToolResult) -> str | None:
    """Name why an unavailable tool did not run, or None when it is available.

    Args:
        result: One tool's result.

    Returns:
        ``"unavailable: <reason>"``, or None.
    """
    if not result.unavailable:
        return None
    why = quote_first_line(result.unavailable_reason, limit=_QUOTE_LIMIT)
    return f"unavailable: {why or 'tool not found'}"
