"""Subprocess execution utilities for tool plugins.

This module provides safe subprocess execution with validation and streaming.
"""

from __future__ import annotations

import contextlib
import os
import subprocess  # nosec B404 - subprocess used safely with shell=False
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from lintro.plugins.subprocess_failure import format_subprocess_failure

if TYPE_CHECKING:
    from collections.abc import Callable

# Cache for compiled binary detection
_IS_COMPILED_BINARY: bool | None = None


@dataclass(frozen=True)
class SubprocessResult:
    """Result of a subprocess execution with separated output streams.

    Historically the subprocess runners concatenated stdout and stderr into a
    single string, which forced every JSON parser to hand-roll recovery from a
    mixed blob (a single stderr warning line could break parsing). This result
    object keeps the streams separated so tool definitions can parse stdout
    only and log stderr independently. See issue #1043.

    Attributes:
        returncode: The subprocess exit code.
        stdout: Captured standard output. For streaming execution (which merges
            the child's stderr into stdout for real-time line handling) this
            holds the combined stream and ``stderr`` is empty.
        stderr: Captured standard error (empty for streaming execution).
        output: Backward-compatible display string. Normally ``stdout`` and
            ``stderr`` concatenated, or a formatted failure message when the
            command failed without emitting any output.
    """

    returncode: int
    stdout: str
    stderr: str
    output: str

    @property
    def success(self) -> bool:
        """Whether the subprocess exited successfully.

        Returns:
            True when the return code is zero.
        """
        return self.returncode == 0

    def as_tuple(self) -> tuple[bool, str]:
        """Return the legacy ``(success, output)`` representation.

        Returns:
            Tuple of the success flag and the combined/display output, matching
            the pre-#1043 return contract used by most tool definitions.
        """
        return self.success, self.output


def is_compiled_binary() -> bool:
    """Detect if lintro is running as a Nuitka-compiled binary.

    When compiled with Nuitka, sys.executable points to the lintro binary itself,
    not a Python interpreter. We detect this by checking if we can import Nuitka
    runtime modules or by checking the executable name.

    Returns:
        True if running as a compiled binary, False otherwise.
    """
    global _IS_COMPILED_BINARY

    if _IS_COMPILED_BINARY is not None:
        return _IS_COMPILED_BINARY

    # Method 1: Check for Nuitka's __compiled__ marker
    try:
        # Nuitka sets __compiled__ at module level
        import __main__

        if getattr(__main__, "__compiled__", False):
            _IS_COMPILED_BINARY = True
            return True
    except (ImportError, AttributeError):
        pass

    # Method 2: Check if sys.executable looks like our binary (not python)
    exe_name = os.path.basename(sys.executable).lower()
    if exe_name in ("lintro", "lintro.exe", "lintro.bin"):
        _IS_COMPILED_BINARY = True
        return True

    # Method 3: Check if we're running from a Nuitka dist folder
    exe_dir = os.path.dirname(sys.executable)
    if "nuitka" in exe_dir.lower() or "__nuitka" in exe_dir.lower():
        _IS_COMPILED_BINARY = True
        return True

    _IS_COMPILED_BINARY = False
    return False


# Shell metacharacters that could enable command injection or unexpected behavior.
# Using frozenset for immutability and O(1) membership testing.
UNSAFE_SHELL_CHARS: frozenset[str] = frozenset(
    {
        # Command chaining and piping
        ";",  # Command separator
        "&",  # Background execution / AND operator
        "|",  # Pipe
        # Redirection
        ">",  # Output redirection
        "<",  # Input redirection
        # Command substitution and expansion
        "`",  # Backtick command substitution
        "$",  # Variable expansion / command substitution
        # Escape and control characters
        "\\",  # Escape character
        "\n",  # Newline (command separator in some contexts)
        "\r",  # Carriage return
        # Glob and pattern matching
        "*",  # Glob wildcard (match any)
        "?",  # Glob wildcard (match single char)
        "[",  # Character class start
        "]",  # Character class end
        # Brace and subshell expansion
        "{",  # Brace expansion start
        "}",  # Brace expansion end
        "(",  # Subshell start
        ")",  # Subshell end
        # Other shell special characters
        "~",  # Home directory expansion
        "!",  # History expansion
    },
)


def validate_subprocess_command(cmd: list[str]) -> None:
    """Validate a subprocess command for safety.

    Since lintro uses shell=False for all subprocess calls, command arguments
    are passed directly to the executable without shell interpretation. This
    means characters like $, *, {, } in arguments are safe - they won't be
    expanded by the shell.

    We only validate the command name (first element) to ensure it doesn't
    contain shell metacharacters that could indicate a path traversal or
    injection attempt.

    Args:
        cmd: Command and arguments to validate.

    Raises:
        ValueError: If command is invalid or the command name contains
            unsafe characters.
    """
    if not cmd or not isinstance(cmd, list):
        raise ValueError("Command must be a non-empty list of strings")

    for arg in cmd:
        if not isinstance(arg, str):
            raise ValueError("All command arguments must be strings")

    # Only validate the command name (first element) for shell metacharacters.
    # Arguments are safe with shell=False as they're passed literally.
    if any(ch in cmd[0] for ch in UNSAFE_SHELL_CHARS):
        raise ValueError("Unsafe character detected in command name")


def run_subprocess(
    cmd: list[str],
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> SubprocessResult:
    """Run a subprocess command safely.

    Args:
        cmd: Command and arguments to run.
        timeout: Timeout in seconds.
        cwd: Working directory for command execution.
        env: Environment variables for the subprocess. These are merged with
            os.environ to preserve PATH and other essential variables.

    Returns:
        SubprocessResult with the return code and separated stdout/stderr
        streams (plus a combined ``output`` display string for compatibility).

    Raises:
        subprocess.TimeoutExpired: If command times out.
        FileNotFoundError: If command executable is not found.
    """
    validate_subprocess_command(cmd)

    cmd_str = " ".join(cmd[:5]) + ("..." if len(cmd) > 5 else "")
    logger.debug(f"Running subprocess: {cmd_str} (timeout={timeout}s, cwd={cwd})")

    # Merge custom env with os.environ to preserve PATH, HOME, etc.
    # Custom env values override os.environ when there are conflicts.
    effective_env: dict[str, str] | None = None
    if env is not None:
        effective_env = {**os.environ, **env}

    try:
        result = subprocess.run(  # nosec B603 - args list, shell=False
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=effective_env,
        )

        combined = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            stderr_preview = (result.stderr or "")[:500]
            if stderr_preview:
                logger.debug(
                    f"Subprocess {cmd[0]} exited with code {result.returncode}, "
                    f"stderr: {stderr_preview}",
                )
            if not combined.strip():
                combined = format_subprocess_failure(
                    tool_name=cmd[0],
                    cmd=cmd,
                    returncode=result.returncode,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    cwd=cwd,
                    timeout=timeout,
                )
                logger.warning(combined)

        return SubprocessResult(
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            output=combined,
        )
    except subprocess.TimeoutExpired as e:
        logger.warning(f"Subprocess {cmd[0]} timed out after {timeout}s")
        # Preserve partial output from the original exception
        partial_output = ""
        if e.output:
            partial_output = (
                e.output
                if isinstance(e.output, str)
                else e.output.decode(errors="replace")
            )
        if e.stderr:
            stderr = (
                e.stderr
                if isinstance(e.stderr, str)
                else e.stderr.decode(errors="replace")
            )
            partial_output = partial_output + stderr if partial_output else stderr
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout,
            output=partial_output,
        ) from e
    except FileNotFoundError as e:
        logger.warning(
            f"Command not found: {cmd[0]}. Ensure it is installed and in PATH.",
        )
        raise FileNotFoundError(
            f"Command not found: {cmd[0]}. "
            f"Please ensure it is installed and in your PATH.",
        ) from e


def run_subprocess_streaming(
    cmd: list[str],
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    line_handler: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """Run a subprocess command with optional line-by-line streaming.

    This function allows real-time output processing by calling the line_handler
    callback for each line of output as it is produced by the subprocess.

    The timeout is enforced during both output reading and process completion,
    preventing indefinite blocking on slow or hanging processes.

    Streaming execution merges the child's stderr into stdout so the line
    handler observes a single ordered stream; the returned ``stderr`` field is
    therefore empty and ``stdout`` carries the combined stream.

    Args:
        cmd: Command and arguments to run.
        timeout: Timeout in seconds.
        cwd: Working directory for command execution.
        env: Environment variables for the subprocess. These are merged with
            os.environ to preserve PATH and other essential variables.
        line_handler: Optional callback called for each line of output.

    Returns:
        SubprocessResult with the return code and captured output. ``stdout``
        holds the merged stream, ``stderr`` is empty.

    Raises:
        subprocess.TimeoutExpired: If command times out.
        FileNotFoundError: If command executable is not found.
    """
    validate_subprocess_command(cmd)

    cmd_str = " ".join(cmd[:5]) + ("..." if len(cmd) > 5 else "")
    logger.debug(
        f"Running subprocess (streaming): {cmd_str} (timeout={timeout}s, cwd={cwd})",
    )

    # Merge custom env with os.environ to preserve PATH, HOME, etc.
    # Custom env values override os.environ when there are conflicts.
    effective_env: dict[str, str] | None = None
    if env is not None:
        effective_env = {**os.environ, **env}

    try:
        # Use Popen for streaming output
        # Args list, shell=False
        process = subprocess.Popen(  # nosec B603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=effective_env,
            bufsize=1,  # Line buffering
        )

        output_lines: list[str] = []

        def read_output() -> None:
            """Read output lines in a separate thread."""
            if process.stdout:
                for line in process.stdout:
                    stripped = line.rstrip("\n")
                    output_lines.append(stripped)
                    if line_handler:
                        line_handler(stripped)

        # Use a thread to read output so we can enforce timeout.
        # Track elapsed wall time so the reader join and the subsequent
        # process.wait() share a single timeout budget rather than each
        # receiving the full timeout (which could allow ~2x the configured
        # limit). See issue #1047.
        start_time = time.monotonic()
        reader_thread = threading.Thread(target=read_output, daemon=True)
        reader_thread.start()
        reader_thread.join(timeout=timeout)

        if reader_thread.is_alive():
            # Timeout occurred during reading - kill the process
            logger.warning(
                f"Subprocess {cmd[0]} timed out after {timeout}s (reading output)",
            )
            process.kill()
            # Brief timeout for cleanup; ignore if process doesn't die cleanly
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1.0)
            raise subprocess.TimeoutExpired(
                cmd=cmd,
                timeout=timeout,
                output="\n".join(output_lines),
            )

        # Reading completed, now wait for process to finish using only the
        # remaining slice of the timeout budget.
        elapsed = time.monotonic() - start_time
        remaining_timeout = max(0.0, timeout - elapsed)
        try:
            returncode = process.wait(timeout=remaining_timeout)
        except subprocess.TimeoutExpired as e:
            logger.warning(
                f"Subprocess {cmd[0]} timed out after {timeout}s (during wait)",
            )
            process.kill()
            process.wait(timeout=1.0)
            raise subprocess.TimeoutExpired(
                cmd=cmd,
                timeout=timeout,
                output="\n".join(output_lines),
            ) from e

        raw_output = "\n".join(output_lines)
        output = raw_output
        if returncode != 0:
            output_preview = output[:500]
            if output_preview:
                logger.debug(
                    f"Subprocess {cmd[0]} exited with code {returncode}, "
                    f"output: {output_preview}",
                )
            if not output.strip():
                output = format_subprocess_failure(
                    tool_name=cmd[0],
                    cmd=cmd,
                    returncode=returncode,
                    cwd=cwd,
                    timeout=timeout,
                )
                logger.warning(output)

        return SubprocessResult(
            returncode=returncode,
            stdout=raw_output,
            stderr="",
            output=output,
        )

    except FileNotFoundError as e:
        logger.warning(
            f"Command not found: {cmd[0]}. Ensure it is installed and in PATH.",
        )
        raise FileNotFoundError(
            f"Command not found: {cmd[0]}. "
            f"Please ensure it is installed and in your PATH.",
        ) from e
