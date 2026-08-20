"""Per-tool status values in JSON reports and ``list-tools`` output."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING

from lintro.enums.tool_run_status import ToolRunStatus, tool_run_status
from lintro.formatters.formatter import merge_detected_and_remaining

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult

_RUN_STATUS_TO_RESULT_STATUS: dict[ToolRunStatus, "ToolResultStatus"] = {}


class ToolResultStatus(StrEnum):
    """Status of one tool in a serialized run or ``list-tools`` listing.

    Attributes:
        OK: The tool ran and succeeded (or is runtime-available with a version).
        FAILED: The tool ran and failed, timed out, or reported issues.
        SKIPPED: The tool was skipped (version gate, no files, disabled).
        UNAVAILABLE: The tool binary could not be probed or is missing.
        UNKNOWN: Capability could not be determined (no snapshot or no version).
    """

    OK = auto()
    FAILED = auto()
    SKIPPED = auto()
    UNAVAILABLE = auto()
    UNKNOWN = auto()


_RUN_STATUS_TO_RESULT_STATUS.update(
    {
        ToolRunStatus.PASSED: ToolResultStatus.OK,
        ToolRunStatus.ISSUES: ToolResultStatus.FAILED,
        ToolRunStatus.SKIPPED: ToolResultStatus.SKIPPED,
        ToolRunStatus.TIMED_OUT: ToolResultStatus.FAILED,
        ToolRunStatus.ERRORED: ToolResultStatus.FAILED,
    },
)


def status_for_tool_result(
    result: ToolResult,
    *,
    issue_count: int | None = None,
) -> ToolResultStatus:
    """Derive the JSON ``status`` field for a completed tool result.

    Classification follows :func:`~lintro.enums.tool_run_status.tool_run_status`
    so timeouts and issue counts are not collapsed into ``ok``/``failed``
    incorrectly. ``unavailable`` is checked first because it is outside the run
    status vocabulary.

    Args:
        result: Tool result from a check/format/test run.
        issue_count: Optional precomputed issue count. When omitted, merged
            initial and remaining issues are counted like
            :func:`~lintro.utils.json_output.serialize_tool_result`.

    Returns:
        ToolResultStatus: ``unavailable``, ``skipped``, ``ok``, or ``failed``.
    """
    if getattr(result, "unavailable", False):
        return ToolResultStatus.UNAVAILABLE
    if issue_count is None:
        merged_issues = merge_detected_and_remaining(
            getattr(result, "initial_issues", None),
            getattr(result, "issues", None),
        )
        issue_count = len(merged_issues)
    run_status = tool_run_status(result=result, issue_count=issue_count)
    return _RUN_STATUS_TO_RESULT_STATUS[run_status]
