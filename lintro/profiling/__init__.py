"""Performance profiling for lintro tool execution.

Captures per-tool wall-clock timing during check/fmt runs and renders it as an
opt-in (``--profile``) report and JSON payload, helping users find slow tools
and optimize their setup.
"""

from lintro.profiling.report import (
    build_profile_data,
    build_timings,
    render_profile_report,
)
from lintro.profiling.suggestions import get_suggestions
from lintro.profiling.timer import Timer, ToolTiming

__all__ = [
    "Timer",
    "ToolTiming",
    "build_profile_data",
    "build_timings",
    "get_suggestions",
    "render_profile_report",
]
