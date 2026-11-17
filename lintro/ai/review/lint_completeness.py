"""Tell lint results that answered apart from ones that did not (#3497).

The review prompt's lint digest must never read a tool's silence as a clean
result when the tool did not run or did not complete, and such a tool is not
counted among the tools that ran. A saved ``--lint-report`` must give the
same answer as the live run it records, so this module also rebuilds every
completeness field the report writer emits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.result_reason import incomplete_reason

__all__ = ["LintRunCounts", "count_lint_results", "report_tool_result"]

#: Boolean completeness markers the report writer emits when they are set.
_REPORT_FLAGS: tuple[str, ...] = (
    "execution_failed",
    "timed_out",
    "residual_unknown",
    "skipped",
    "unavailable",
)


@dataclass(frozen=True)
class LintRunCounts:
    """How many lint tools answered, how many did not, and what they found.

    Attributes:
        ran: Tools that produced a complete verdict. A skipped or unavailable
            tool is counted in neither field: it made no claim either way.
        incomplete: Tools that did not run or did not complete.
        issues: Findings reported by all tools, incomplete ones included.
    """

    ran: int
    incomplete: int
    issues: int


def count_lint_results(results: list[ToolResult]) -> LintRunCounts:
    """Count the tools that ran apart from the ones that did not complete.

    Args:
        results: Tool results from a lint run or a saved report.

    Returns:
        The counts, so neither a refused nor a skipped tool is counted as
        one that ran.
    """
    incomplete = sum(1 for result in results if incomplete_reason(result))
    no_claim = sum(1 for result in results if result.skipped or result.unavailable)
    return LintRunCounts(
        ran=len(results) - incomplete - no_claim,
        incomplete=incomplete,
        issues=sum(result.issues_count or 0 for result in results),
    )


def _optional(entry: Mapping[str, Any], key: str, kind: type) -> Any:
    """Read an optional typed field of a report entry.

    Args:
        entry: One per-tool report object.
        key: Field name.
        kind: The type the field must have when present and not null.

    Returns:
        The value, or None when absent or null.

    Raises:
        ValueError: When the field has another type (``bool`` is never
            accepted as an ``int``).
    """
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        msg = f"has a non-{kind.__name__} {key!r}"
        raise ValueError(msg)
    return value


def report_tool_result(
    entry: Mapping[str, Any],
    *,
    name: str,
    issues: Sequence[BaseIssue],
) -> ToolResult:
    """Rebuild one tool's result from a saved report, completeness included.

    Every marker the report writer emits (``execution_failed``,
    ``timed_out``, ``residual_unknown``, ``parse_failures_count``,
    ``skipped`` and ``unavailable``, with their reasons) is carried, so the
    reloaded result is judged exactly as the live one was. The report is
    untrusted, so a contradiction fails closed: an incomplete marker wins
    over a skip or an unavailable claim, and so do findings.

    Args:
        entry: One per-tool report object.
        name: The tool's name.
        issues: Its parsed findings.

    Returns:
        The rebuilt result.

    Raises:
        ValueError: When a completeness field has the wrong type.
    """
    flags = {key: bool(_optional(entry, key, bool)) for key in _REPORT_FLAGS}
    parse_failures = _optional(entry, "parse_failures_count", int)
    if parse_failures is not None and parse_failures < 0:
        msg = "has a negative 'parse_failures_count'"
        raise ValueError(msg)
    output = _optional(entry, "output", str)
    incomplete = bool(
        flags["execution_failed"]
        or flags["timed_out"]
        or flags["residual_unknown"]
        or parse_failures,
    )
    success = bool(entry.get("success", True)) and not incomplete
    if flags["unavailable"] and not incomplete and not issues:
        reason = _optional(entry, "unavailable_reason", str) or "tool not found"
        return ToolResult.from_outcome(
            ToolOutcome(execution=Unavailable(reason=reason, strict=not success)),
            name=name,
            output=output,
        )
    skipped = flags["skipped"] and not incomplete and not issues
    residual_reason = _optional(entry, "residual_unknown_reason", str)
    return ToolResult(
        name=name,
        success=success or skipped,
        issues_count=len(issues),
        issues=list(issues),
        output=output,
        execution_failed=flags["execution_failed"],
        timed_out=flags["timed_out"],
        residual_unknown=flags["residual_unknown"],
        residual_unknown_reason=(
            (residual_reason or "remaining issues unknown")
            if flags["residual_unknown"]
            else None
        ),
        parse_failures_count=parse_failures,
        skipped=skipped,
        skip_reason=(
            (_optional(entry, "skip_reason", str) or "skipped") if skipped else None
        ),
    )
