"""Per-tool status values in JSON reports and ``list-tools`` output."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


class ToolResultStatus(StrEnum):
    """Status of one tool in a serialized run or ``list-tools`` listing.

    Attributes:
        OK: The tool ran and succeeded (or is runtime-available).
        FAILED: The tool ran and failed.
        SKIPPED: The tool was skipped (version gate, no files, disabled).
        UNAVAILABLE: The tool binary could not be probed or is missing.
    """

    OK = auto()
    FAILED = auto()
    SKIPPED = auto()
    UNAVAILABLE = auto()


def status_for_tool_result(result: ToolResult) -> ToolResultStatus:
    """Derive the JSON ``status`` field for a completed tool result.

    Args:
        result: Tool result from a check/format/test run.

    Returns:
        ToolResultStatus: ``unavailable``, ``skipped``, ``ok``, or ``failed``.
    """
    if getattr(result, "unavailable", False):
        return ToolResultStatus.UNAVAILABLE
    if getattr(result, "skipped", False):
        return ToolResultStatus.SKIPPED
    if getattr(result, "success", True):
        return ToolResultStatus.OK
    return ToolResultStatus.FAILED
