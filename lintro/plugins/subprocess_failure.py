"""Helpers for surfacing actionable subprocess failure diagnostics."""

from __future__ import annotations

import shlex


def format_subprocess_failure(
    *,
    tool_name: str,
    cmd: list[str],
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    cwd: str | None = None,
    timeout: float | None = None,
) -> str:
    """Build a user-facing message when a tool subprocess fails.

    Args:
        tool_name: Human-readable tool name (e.g. ``mypy``).
        cmd: Command argv that was executed.
        returncode: Process exit code from ``subprocess.run``.
        stdout: Captured standard output.
        stderr: Captured standard error.
        cwd: Working directory used for the subprocess, if any.
        timeout: Configured timeout in seconds, if known.

    Returns:
        Multi-line diagnostic string suitable for console and CI logs.
    """
    combined = (stdout or "") + (stderr or "")
    if combined.strip():
        return combined

    cmd_preview = shlex.join(cmd[:8]) + (" ..." if len(cmd) > 8 else "")
    lines = [
        f"{tool_name} execution failed (exit code {returncode}).",
        f"Command: {cmd_preview}",
    ]
    if cwd:
        lines.append(f"Working directory: {cwd}")
    if timeout is not None:
        lines.append(f"Timeout: {timeout}s")

    if returncode < 0:
        signal = -returncode
        lines.append(f"Process terminated by signal {signal}.")
    elif returncode == 137 or returncode == 143:
        lines.append(
            "Process was likely killed (OOM or timeout). "
            "Try reducing parallel workers or increasing tool timeout in CI.",
        )
    elif returncode == 126:
        lines.append("Command found but not executable.")
    elif returncode == 127:
        lines.append("Command not found on PATH inside the execution environment.")

    lines.append("No stdout/stderr was captured from the subprocess.")
    return "\n".join(lines)
