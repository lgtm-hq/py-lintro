"""Keep ``cmd.exe`` metacharacters off an agent CLI's batch shim (#3494).

Agent CLIs installed with npm on Windows (``claude``, ``codex``, the Cursor
``agent``) usually resolve to a ``.cmd`` shim. ``CreateProcess`` runs a
``.cmd`` or ``.bat`` argv0 through ``cmd.exe``, which parses the whole command
line even though the transport spawns without a shell, and
``subprocess.list2cmdline`` quotes for the C runtime, not for ``cmd.exe``: a
``"`` in a JSON schema, or ``&`` in a repository path, lets the rest of the
line be read as commands (BatBadBut, CVE-2024-24576). #3486 closed that for
lint tools in :func:`lintro.plugins.subprocess_executor.validate_subprocess_command`;
the AI transports spawn through ``asyncio.create_subprocess_exec`` and need
the same refusal.

Providers move what they can off the command line first (the prompt already
rides on stdin, the Cursor workspace goes through the working directory on a
shim). What is left and still carries a character ``cmd.exe`` interprets is
refused here, before anything is spawned.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

from lintro.ai.exceptions import AINotAvailableError
from lintro.exceptions.errors import UnsafeCommandError
from lintro.plugins.subprocess_executor import (
    BATCH_UNSAFE_CHARS,
    is_batch_command,
    validate_subprocess_command,
)

__all__ = ["refuse_unsafe_batch_argv"]


def _argument_label(cmd: list[str], index: int) -> str:
    """Name ``cmd[index]`` by its flag, without echoing its value.

    The value may be a whole system prompt or schema, so it is not quoted.

    Args:
        cmd: The full argv.
        index: Position of the offending argument, at least 1.

    Returns:
        ``--flag`` for an ``--flag=value`` argument, ``the value of --flag``
        when the preceding argument is a flag, otherwise ``argument N``.
    """
    arg = cmd[index]
    if arg.startswith("-"):
        return arg.split("=", 1)[0]
    previous = cmd[index - 1]
    if index > 1 and previous.startswith("-"):
        return f"the value of {previous}"
    return f"argument {index}"


def refuse_unsafe_batch_argv(cmd: list[str], *, binary_name: str) -> None:
    """Refuse an argv that ``cmd.exe`` would interpret on a batch argv0.

    Does nothing unless ``cmd[0]`` is a ``.cmd`` or ``.bat`` file, so a
    native executable keeps receiving any argument it did before. On a batch
    argv0 the command path and every argument are checked against
    :data:`~lintro.plugins.subprocess_executor.BATCH_UNSAFE_CHARS`; spaces
    and parentheses are not in that set, so
    ``C:/Program Files (x86)/nodejs/claude.cmd`` still runs.

    The refusal is an :class:`~lintro.ai.exceptions.AINotAvailableError`:
    the installed binary cannot serve this call, and a retry or a fallback
    model on the same binary would be refused the same way.

    Args:
        cmd: Full argv including the binary path.
        binary_name: Human-readable binary name for the error message.

    Raises:
        AINotAvailableError: Naming the offending flag and the characters
            ``cmd.exe`` would interpret in its value.
    """
    if not cmd or not is_batch_command(cmd[0]):
        return
    shim = PureWindowsPath(cmd[0]).name
    remedy = (
        f"Install the {binary_name} CLI so it resolves to an executable "
        "instead of a .cmd/.bat shim, or use `--transport api` where the "
        "provider offers it."
    )
    try:
        validate_subprocess_command([cmd[0]])
    except UnsafeCommandError as exc:
        raise AINotAvailableError(
            f"{binary_name} CLI resolves to the batch shim {cmd[0]!r}, whose "
            f"path holds characters cmd.exe would interpret, so lintro does "
            f"not launch it. {remedy}",
        ) from exc
    for index, arg in enumerate(cmd[1:], start=1):
        found = sorted({ch for ch in arg if ch in BATCH_UNSAFE_CHARS})
        if not found:
            continue
        chars = " ".join(repr(ch) for ch in found)
        raise AINotAvailableError(
            f"{binary_name} CLI resolves to the batch shim {shim!r}, which "
            f"Windows runs through cmd.exe, and {_argument_label(cmd, index)} "
            f"contains {chars} that cmd.exe would interpret as a command "
            f"separator, redirection, escape, quote or variable, so lintro "
            f"does not launch it. {remedy}",
        )
