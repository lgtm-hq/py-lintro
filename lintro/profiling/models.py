"""Data model for lintro performance profiling.

The per-tool wall-clock durations themselves are captured by the executors on
each :class:`~lintro.models.core.tool_result.ToolResult`; this module holds the
record the ``--profile`` report and its JSON payload are built from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class ProfileToolEntry(TypedDict):
    """One tool's object in the ``--profile`` JSON payload.

    Attributes:
        name: Display name of the tool.
        duration: Rounded execution time in seconds.
        files_with_issues: Distinct ``issue.file`` values after the same
            detected/remaining merge used by JSON ``results[]``.
        issues_found: Length of that merged issue list.
    """

    name: str
    duration: float
    files_with_issues: int
    issues_found: int


class ProfileData(TypedDict):
    """JSON-serializable ``--profile`` payload attached at stdout and files.

    Attributes:
        cumulative_tool_duration: Sum of per-tool seconds (not parallel
            wall-clock).
        tools: Per-tool entries sorted slowest first.
        suggestions: Optimization hints derived from the timings.
    """

    cumulative_tool_duration: float
    tools: list[ProfileToolEntry]
    suggestions: list[str]


@dataclass
class ToolTiming:
    """Per-tool timing attribution for a single profiled run.

    Attributes:
        tool: Display name of the tool.
        duration: Wall-clock execution time in seconds.
        files_with_issues: Number of distinct files the tool reported issues
            on (not the number of files scanned).
        issues_found: Number of issues after the same detected/remaining
            merge used by JSON ``results[]``.
    """

    tool: str
    duration: float
    files_with_issues: int = field(default=0)
    issues_found: int = field(default=0)
