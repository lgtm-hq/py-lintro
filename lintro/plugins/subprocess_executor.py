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
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from loguru import logger

from lintro.exceptions.errors import UnsafeCommandError
from lintro.models.core.subprocess_result import SubprocessResult as SubprocessResult
from lintro.plugins.subprocess_failure import format_subprocess_failure
from lintro.utils.npx_call import npx_child_env, npx_shell_safe_command

if TYPE_CHECKING:
    from collections.abc import Callable

# Cache for compiled binary detection
_IS_COMPILED_BINARY: bool | None = None


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

#: Characters never legitimate anywhere in argv0, absolute or not.
COMMAND_CONTROL_CHARS: frozenset[str] = frozenset({"\n", "\r"})

#: Characters ``cmd.exe`` interprets on a batch file's command line. Windows
#: runs a ``.cmd`` or ``.bat`` argv0 through ``cmd.exe /c`` even with
#: ``shell=False``, and ``subprocess.list2cmdline`` quotes for the C runtime,
#: not for ``cmd.exe``: it leaves a value without spaces unquoted and escapes
#: ``"`` as ``\"``, which ``cmd.exe`` reads as a plain quote that toggles its
#: quoting. So ``C:/a&whoami&/x.cmd`` or the argument ``a&whoami&b`` would run
#: ``whoami`` (BatBadBut, CVE-2024-24576). ``%`` is here because ``cmd.exe``
#: expands ``%NAME%`` even inside quotes. ``(``, ``)`` and space are not:
#: ``C:/Program Files (x86)/nodejs/prettier.cmd`` must keep running, and
#: ``list2cmdline`` quotes any value with a space. ``!`` is not either: it
#: only expands under delayed expansion, which ``cmd.exe /c`` leaves off by
#: default. A machine-wide ``DelayedExpansion`` setting or a shim that runs
#: ``setlocal enabledelayedexpansion`` can turn it on, and then ``!NAME!``
#: in an argument expands to a variable's value; that can alter or disclose
#: a value but cannot start a command without one of the characters below.
BATCH_UNSAFE_CHARS: frozenset[str] = frozenset(
    {"&", "|", "<", ">", "^", "%", '"', *COMMAND_CONTROL_CHARS},
)

#: argv0 suffixes Windows launches through ``cmd.exe``, compared lowercased.
BATCH_SUFFIXES: frozenset[str] = frozenset({".cmd", ".bat"})


def is_batch_command(command: str) -> bool:
    """Return whether Windows would launch *command* through ``cmd.exe``.

    Args:
        command: The command name, ``cmd[0]``.

    Returns:
        True when its suffix is ``.cmd`` or ``.bat`` in any letter case,
        ignoring trailing dots and spaces.
    """
    # PureWindowsPath splits on both separators, so mixed paths work too.
    # Windows strips trailing dots and spaces from a path's final component,
    # so ``prettier.cmd.``, ``prettier.cmd. .`` and ``prettier.cmd `` all
    # launch ``prettier.cmd`` through cmd.exe.
    name = PureWindowsPath(command).name.rstrip(". ")
    return PureWindowsPath(name).suffix.lower() in BATCH_SUFFIXES


def _command_name_to_check(command: str) -> str:
    r"""Return the part of argv0 the unsafe-character check applies to.

    An absolute path (POSIX ``/...`` or Windows ``C:/...``, ``C:\...`` or a
    UNC share) is reduced to its final component, so a legal install
    directory such as ``C:/Program Files (x86)/nodejs`` or ``~/tools!`` does
    not trip the check. Anything else, including a bare name and a relative
    path, is checked whole.

    Args:
        command: The command name, ``cmd[0]``.

    Returns:
        The basename for an absolute path, otherwise *command* unchanged.
    """
    if PurePosixPath(command).is_absolute():
        return PurePosixPath(command).name
    # A Windows-shaped path counts as absolute on a POSIX host too. That is
    # harmless: under ``shell=False`` POSIX ``execve`` takes the string as one
    # literal (relative) file name, and the batch check in
    # :func:`validate_subprocess_command` still covers ``.cmd``/``.bat``.
    if PureWindowsPath(command).is_absolute():
        return PureWindowsPath(command).name
    return command


def validate_subprocess_command(cmd: list[str]) -> None:
    """Validate a subprocess command for safety.

    Every lintro subprocess runs with ``shell=False``: the argv list goes to
    ``execve`` (``CreateProcess`` on Windows) without a shell, so no element
    is expanded, split or interpreted, with one exception: Windows launches a
    ``.cmd`` or ``.bat`` argv0 through ``cmd.exe``, which parses the whole
    command line. For any other argv0, shell metacharacters are not an
    injection vector anywhere in the command, and arguments are not checked,
    so ``$``, ``*``, ``{``, ``}`` and ``&`` in them are fine.

    The command name keeps a check as a guard against a malformed or
    smuggled name, and its scope depends on the name's shape (#2040):

    - A bare name or relative path (``prettier``, ``./bin/x``) is checked
      whole against :data:`UNSAFE_SHELL_CHARS`, so ``ls;rm`` or ``x$(id)``
      is still rejected.
    - An absolute path, the shape lintro's own resolution produces
      (``shutil.which`` or ``node_modules/.bin``), is checked on its basename
      only. Directory components may hold any path-legal character, so
      ``C:/Program Files (x86)/nodejs/prettier.cmd`` and paths under ``~`` or
      ``!`` directories run. No character is removed from
      :data:`UNSAFE_SHELL_CHARS`.

    Two checks always cover the whole of argv0, whatever its shape:

    - :data:`COMMAND_CONTROL_CHARS` (newline, carriage return) are rejected
      anywhere; no real install path contains them.
    - A ``.cmd`` or ``.bat`` argv0 is the exception to ``shell=False``:
      Windows launches it through ``cmd.exe``, which parses the path itself.
      Its whole path is checked against :data:`BATCH_UNSAFE_CHARS`, so a
      directory such as ``C:/tools&whoami&/`` is rejected while ``(x86)``
      still passes.

    For a batch argv0 every argument is checked against
    :data:`BATCH_UNSAFE_CHARS` too (#3486), so
    ``["prettier.cmd", "a&whoami&b"]`` is refused instead of running
    ``whoami``, while ``C:/Program Files (x86)/repo/a.ts`` still passes.
    lintro refuses rather than escaping, since ``cmd.exe`` quoting cannot be
    made safe for every batch script (CVE-2024-24576).

    The batch rules apply on every platform, not only on Windows: the suffix
    is what ``CreateProcess`` acts on, so a command is refused the same way
    wherever it is validated, and lintro's own resolution never produces a
    ``.cmd`` or ``.bat`` argv0 off Windows.

    Validation runs on the command as lintro built it. The npx ``--call``
    rewrite (:func:`lintro.utils.npx_call.npx_shell_safe_command`) keeps
    argv0, so the name checked is the name spawned.

    Args:
        cmd: Command and arguments to validate.

    Raises:
        ValueError: If command is not a non-empty list of strings.
        UnsafeCommandError: A ``ValueError`` raised when the checked part of
            the command name, or an argument passed to a batch file, contains
            unsafe characters.
    """
    if not cmd or not isinstance(cmd, list):
        raise ValueError("Command must be a non-empty list of strings")

    for arg in cmd:
        if not isinstance(arg, str):
            raise ValueError("All command arguments must be strings")

    command = cmd[0]
    if any(ch in command for ch in COMMAND_CONTROL_CHARS):
        raise UnsafeCommandError("Unsafe character detected in command name")
    is_batch = is_batch_command(command)
    if is_batch and any(ch in command for ch in BATCH_UNSAFE_CHARS):
        raise UnsafeCommandError("Unsafe character detected in command name")

    checked = _command_name_to_check(command)
    if any(ch in checked for ch in UNSAFE_SHELL_CHARS):
        raise UnsafeCommandError("Unsafe character detected in command name")

    if is_batch:
        _reject_batch_unsafe_arguments(cmd)


def _reject_batch_unsafe_arguments(cmd: list[str]) -> None:
    """Refuse arguments ``cmd.exe`` would interpret for a batch argv0 (#3486).

    Args:
        cmd: Command whose argv0 is a ``.cmd`` or ``.bat`` file.

    Raises:
        UnsafeCommandError: Naming the first offending argument, its position
            and the characters ``cmd.exe`` would interpret in it.
    """
    tool = PureWindowsPath(cmd[0]).name
    for index, arg in enumerate(cmd[1:], start=1):
        found = sorted({ch for ch in arg if ch in BATCH_UNSAFE_CHARS})
        if found:
            chars = " ".join(repr(ch) for ch in found)
            raise UnsafeCommandError(
                f"Refusing to pass argument {index} ({arg!r}) to batch file "
                f"{tool!r}: cmd.exe would interpret {chars} in it as a command "
                "separator, redirection, escape or variable. Rename the path, "
                "or install the tool so it resolves to an executable instead "
                "of a .cmd/.bat shim.",
            )


def run_subprocess(
    cmd: list[str],
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin: int | None = None,
) -> SubprocessResult:
    """Run a subprocess command safely.

    Args:
        cmd: Command and arguments to run.
        timeout: Timeout in seconds.
        cwd: Working directory for command execution.
        env: Environment variables for the subprocess. These are merged with
            os.environ to preserve PATH and other essential variables.
        stdin: Optional stdin handle for the subprocess. Defaults to ``None``
            (inherit). Pass ``subprocess.DEVNULL`` to prevent tools that prompt
            interactively from blocking when no TTY is attached.

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
    # Under the npx fallback, Node preloads must not stand in for the pinned
    # tool: drop npm's node-options and code-loading NODE_OPTIONS (#3460).
    effective_env = npx_child_env(cmd, effective_env)

    # npx runs the tool through ``sh -c``; quote its arguments, and keep any
    # script file the long form needs until the process has finished (#3439).
    spawn_scope = contextlib.ExitStack()
    try:
        spawn_cmd = spawn_scope.enter_context(
            npx_shell_safe_command(cmd, timeout=timeout),
        )
        result = subprocess.run(  # nosec B603 - args list, shell=False
            spawn_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=effective_env,
            stdin=stdin,
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
    finally:
        spawn_scope.close()


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

    ``subprocess.TimeoutExpired`` (on timeout) and ``FileNotFoundError`` (when
    the executable is missing) propagate from :func:`_stream_subprocess`.

    Returns:
        SubprocessResult with the return code and captured output. ``stdout``
        holds the merged stream, ``stderr`` is empty.
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
    # Under the npx fallback, Node preloads must not stand in for the pinned
    # tool: drop npm's node-options and code-loading NODE_OPTIONS (#3460).
    effective_env = npx_child_env(cmd, effective_env)

    # npx runs the tool through ``sh -c``; quote its arguments, and keep any
    # script file the long form needs until the process has finished (#3439).
    with npx_shell_safe_command(cmd, timeout=timeout) as spawn_cmd:
        return _stream_subprocess(
            cmd=cmd,
            spawn_cmd=spawn_cmd,
            timeout=timeout,
            cwd=cwd,
            env=effective_env,
            line_handler=line_handler,
        )


def _stream_subprocess(
    *,
    cmd: list[str],
    spawn_cmd: list[str],
    timeout: float,
    cwd: str | None,
    env: dict[str, str] | None,
    line_handler: Callable[[str], None] | None,
) -> SubprocessResult:
    """Spawn *spawn_cmd* and stream its merged output.

    Args:
        cmd: Command as lintro built it, for messages.
        spawn_cmd: Argv actually spawned (see :func:`npx_shell_safe_command`).
        timeout: Timeout in seconds.
        cwd: Working directory for command execution.
        env: Full environment for the subprocess, or None to inherit.
        line_handler: Optional callback called for each line of output.

    Returns:
        SubprocessResult with the merged stream in ``stdout``.

    Raises:
        subprocess.TimeoutExpired: If command times out.
        FileNotFoundError: If command executable is not found.
    """
    try:
        # Use Popen for streaming output
        # Args list, shell=False
        process = subprocess.Popen(  # nosec B603
            spawn_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=env,
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
