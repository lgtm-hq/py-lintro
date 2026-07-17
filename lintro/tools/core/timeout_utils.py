"""Shared timeout handling utilities for tool implementations.

This module provides standardized timeout handling across different tools,
ensuring consistent behavior and error messages for subprocess timeouts.
"""

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class TimeoutResult:
    """Timeout result structure."""

    success: bool
    output: str
    issues_count: int
    issues: list[Any] = field(default_factory=list)
    timed_out: bool = True
    timeout_seconds: int = 0


def run_subprocess_with_timeout(
    tool: Any,
    cmd: list[str],
    timeout: int | None = None,
    cwd: str | None = None,
    tool_name: str | None = None,
) -> tuple[bool, str]:
    """Run a subprocess command with timeout handling.

    This is a wrapper around tool._run_subprocess that provides consistent
    timeout error handling and messaging across different tools.

    Timeout resolution policy:
        Only ``None`` triggers the fallback to the tool's configured/default
        timeout. Any explicit numeric value provided by the caller is
        preserved verbatim, including ``0``. A ``timeout`` of ``0`` is treated
        as a valid caller-provided value (an effectively immediate deadline)
        and is reported as-is in error messages rather than being silently
        replaced by the default. Callers that want no timeout must pass
        ``None`` explicitly.

    Args:
        tool: Tool instance with _run_subprocess method.
        cmd: Command to run.
        timeout: Timeout in seconds. If None, uses tool's configured or default
            timeout. An explicit ``0`` is preserved (not treated as missing).
        cwd: Working directory for command execution.
        tool_name: Name of the tool for error messages. If None, uses tool.name.

    Returns:
        tuple[bool, str]: (success, output) where success is True if command
        succeeded without timeout, and output contains command output or
        timeout error message.

    Raises:
        subprocess.TimeoutExpired: If command times out (re-raised with context).
    """
    tool_name = tool_name or tool.definition.name

    try:
        success, output = tool._run_subprocess(cmd=cmd, timeout=timeout, cwd=cwd)
        return bool(success), str(output)
    except subprocess.TimeoutExpired as e:
        # Re-raise with more context for the calling tool. Only ``None`` falls
        # back to the configured/default timeout; an explicit ``0`` (or any
        # other numeric value) is preserved so the message reflects the actual
        # deadline that was enforced.
        actual_timeout = (
            timeout
            if timeout is not None
            else tool.options.get("timeout", tool._default_timeout)
        )
        timeout_msg = (
            f"{tool_name} execution timed out ({actual_timeout}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options timeout=N\n"
            "  - Command hanging due to external dependencies\n"
        )
        logger.warning(timeout_msg)

        # Create a new TimeoutExpired with enhanced message
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=actual_timeout,
            output=timeout_msg,
        ) from e


def get_timeout_value(tool: Any, default_timeout: int | None = None) -> int:
    """Get timeout value from tool options with fallback to default.

    Args:
        tool: Tool instance with options.
        default_timeout: Default timeout if not specified in options.

    Returns:
        int: Timeout value in seconds.
    """
    if default_timeout is None:
        default_timeout = getattr(tool, "_default_timeout", 300)

    return int(tool.options.get("timeout", default_timeout))


def create_timeout_result(
    tool: Any,
    timeout: int,
    cmd: list[str] | None = None,
    tool_name: str | None = None,
) -> TimeoutResult:
    """Create a standardized timeout result.

    Args:
        tool: Tool instance.
        timeout: Timeout value that was exceeded.
        cmd: Optional command that timed out.
        tool_name: Optional tool name override.

    Returns:
        TimeoutResult: Result dataclass with timeout information.
    """
    tool_name = tool_name or tool.definition.name

    return TimeoutResult(
        success=False,
        output=(
            f"{tool_name} execution timed out ({timeout}s limit exceeded).\n\n"
            "This may indicate:\n"
            "  - Large codebase taking too long to process\n"
            "  - Need to increase timeout via --tool-options timeout=N\n"
            "  - Command hanging due to external dependencies\n"
        ),
        issues_count=1,  # Count timeout as execution failure
        issues=[],
        timed_out=True,
        timeout_seconds=timeout,
    )
