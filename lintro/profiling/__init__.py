"""Performance profiling for lintro tool execution.

Renders the per-tool wall-clock timings the executors record on each
:class:`~lintro.models.core.tool_result.ToolResult` as an opt-in
(``--profile``) report and JSON payload, helping users find slow tools and
optimize their setup.
"""

from lintro.profiling.models import ToolTiming
from lintro.profiling.report import (
    build_profile_data,
    build_timings,
    render_profile_report,
)
from lintro.profiling.suggestions import get_suggestions

__all__ = [
    "ToolTiming",
    "build_profile_data",
    "build_timings",
    "get_suggestions",
    "render_profile_report",
]
