"""Console utilities for Lintro output formatting.

This package provides console output functionality:
- Constants (emojis, borders, regex patterns)
- ThreadSafeConsoleLogger class for thread-safe formatted output with message tracking
"""

from pathlib import Path
from typing import Any

from lintro.utils.console.constants import (
    BORDER_LENGTH,
    DEFAULT_EMOJI,
    DEFAULT_REMAINING_COUNT,
    INFO_BORDER_LENGTH,
    RE_CANNOT_AUTOFIX,
    RE_REMAINING_OR_CANNOT,
    TOOL_EMOJIS,
    get_summary_value,
    get_tool_emoji,
)
from lintro.utils.console.logger import ThreadSafeConsoleLogger


def create_logger(
    run_dir: Path | None = None,
    *,
    route_stderr: bool = False,
    **kwargs: Any,
) -> ThreadSafeConsoleLogger:
    """Create a new ThreadSafeConsoleLogger instance.

    Args:
        run_dir: Optional run directory path for output location display.
        route_stderr: When True, decorative console output is routed to stderr
            so stdout carries only the final machine-readable document.
        **kwargs: Additional arguments (ignored for backward compatibility).

    Returns:
        ThreadSafeConsoleLogger: A new instance of ThreadSafeConsoleLogger.
    """
    return ThreadSafeConsoleLogger(run_dir=run_dir, route_stderr=route_stderr)


__all__ = [
    # Constants
    "TOOL_EMOJIS",
    "DEFAULT_EMOJI",
    "BORDER_LENGTH",
    "INFO_BORDER_LENGTH",
    "DEFAULT_REMAINING_COUNT",
    "RE_CANNOT_AUTOFIX",
    "RE_REMAINING_OR_CANNOT",
    # Functions
    "get_tool_emoji",
    "get_summary_value",
    "create_logger",
    # Classes
    "ThreadSafeConsoleLogger",
]
