"""Data model for lintro performance profiling.

The per-tool wall-clock durations themselves are captured by the executors on
each :class:`~lintro.models.core.tool_result.ToolResult`; this module holds the
record the ``--profile`` report and its JSON payload are built from.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolTiming:
    """Per-tool timing attribution for a single profiled run.

    Attributes:
        tool: Display name of the tool.
        duration: Wall-clock execution time in seconds.
        files_checked: Number of distinct files the tool reported issues on.
        issues_found: Number of issues the tool reported.
    """

    tool: str
    duration: float
    files_checked: int = field(default=0)
    issues_found: int = field(default=0)
