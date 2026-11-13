r"""Hand npx a fully quoted script so its shell cannot expand tool arguments.

npx runs the tool through a shell, not ``execve``: ``libnpmexec`` passes the
command to ``@npmcli/run-script``, which spawns ``sh -c "<bin> <args>"`` (or
the configured ``script-shell``) on every non-Windows platform. Before joining,
``@npmcli/promise-spawn``'s ``escape.sh`` quotes an argument only when it
contains one of ``\t\n\r "#$&'()*;<>?\\`|~``. ``[``, ``]``, ``{``, ``}`` and
``,`` are not in that set, so ``[slug].ts`` reaches the shell bare and globs
to an existing ``s.ts``, and under a bash ``script-shell`` ``a{1,2}.ts``
brace-expands to ``a1.ts a2.ts`` (#3439).

Quoting each element before npx sees it does not help: npm would quote the
quotes again. ``npx --call <script>`` is the documented way to hand npm the
whole command line (``npx --package=<pkg> -c '<cmd> [args...]'``). npm runs
that string unchanged, with the package's ``bin`` directory on ``PATH``, and
appends nothing, so lintro quotes every element itself with
:func:`shlex.join` and the shell sees literals.

A script longer than :data:`NPX_INLINE_SCRIPT_LIMIT` bytes goes to a temporary
file instead, and npm is handed ``. '<file>'``. Linux caps a single argv or
environment string at 131,072 bytes (``MAX_ARG_STRLEN``), and npm copies the
``--call`` string into both the ``sh -c`` argument and ``npm_lifecycle_script``.
:func:`shlex.join` escapes a single quote in five bytes where npm's own escaping
takes four, so without the file a long file list npm alone still fitted would
fail with ``E2BIG``. The file does not ``exec`` the tool: the shell stays its
parent, so a signal-killed tool exits 128+N as it does inline under dash.

The shell itself is pinned too (#3441). npm reads ``script-shell`` from a
project ``.npmrc`` (the one in the directory npx runs from, so a nested file
counts when a tool runs from a subdirectory), from ``npm_config_script_shell``
and from the user and global npmrc files. Any executable named there replaces
``sh`` and receives the script, so it could print ``[]``, exit 0 and stand in
for the pinned tool. Every rewritten command therefore carries
:data:`NPX_SCRIPT_SHELL_OPTION` before ``--package``: npm gives a command-line
flag precedence over the environment and every npmrc file (checked with npm
10.9.9 and 11.20.0). The sourced script file runs under the same ``/bin/sh``.

Windows is left alone: there npm uses ``cmd.exe``, which does not glob, and
POSIX quoting would be wrong for it.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

#: npx/bunx flags that take the package spec as the next argument.
PACKAGE_FLAGS: frozenset[str] = frozenset({"-p", "--package"})

#: Longest ``--call`` script, in bytes, passed inline. Linux's per-string limit
#: is 131,072 bytes; the margin leaves room for npm's own wrapping.
NPX_INLINE_SCRIPT_LIMIT: int = 96 * 1024

#: Shell npm runs the ``--call`` script with on POSIX. An absolute path, so
#: neither an npmrc ``script-shell`` nor a ``sh`` earlier on ``PATH`` can stand
#: in for it (#3441).
NPX_SCRIPT_SHELL: str = "/bin/sh"

#: The npx option that forces :data:`NPX_SCRIPT_SHELL` over npm configuration.
NPX_SCRIPT_SHELL_OPTION: str = f"--script-shell={NPX_SCRIPT_SHELL}"

#: A command line ready to spawn. A plain name keeps pydoclint from misreading
#: the nested ``Iterator[list[str]]`` yield type as ``str`` (DOC404).
Argv = list[str]


def _split_npx_fallback(
    cmd: Sequence[str],
    *,
    windows: bool | None,
) -> tuple[list[str], str] | None:
    """Split an npx fallback into its runner prefix and quoted tool script.

    Args:
        cmd: Command about to be spawned.
        windows: Whether npx runs on Windows; None means ``os.name == "nt"``.

    Returns:
        The ``[npx, *options, --script-shell=/bin/sh, --package, spec]`` prefix
        and the quoted binary and arguments, or None for any command that is
        not lintro's POSIX npx fallback.
    """
    command = list(cmd)
    if windows is None:
        windows = os.name == "nt"
    if windows or not command or Path(command[0]).name != "npx":
        return None
    for index in range(1, len(command)):
        if command[index] in PACKAGE_FLAGS:
            break
        if not command[index].startswith("--"):
            # A positional before --package is not the shape lintro builds.
            return None
    else:
        return None
    script_start = index + 2
    if script_start >= len(command):
        return None
    prefix = [*command[:index], NPX_SCRIPT_SHELL_OPTION, *command[index:script_start]]
    return prefix, shlex.join(command[script_start:])


@contextlib.contextmanager
def npx_shell_safe_command(
    cmd: Sequence[str],
    *,
    windows: bool | None = None,
    inline_limit: int = NPX_INLINE_SCRIPT_LIMIT,
) -> Iterator[Argv]:
    """Yield the argv to spawn so npm's shell sees every argument literally.

    ``[npx, *options, --package, spec, binary, *args]`` becomes
    ``[npx, *options, --script-shell=/bin/sh, --package, spec, --call,
    "<quoted binary and args>"]``, so neither the arguments nor npm's
    configured ``script-shell`` can change what runs.
    A script longer than *inline_limit* bytes is written to a temporary file
    that ``--call`` sources; the file is deleted when the context exits, so
    keep the context open until the process has finished. Every other command,
    including ``bunx`` and direct binaries, is yielded unchanged.

    Args:
        cmd: Command about to be spawned.
        windows: Whether npx runs on Windows; defaults to ``os.name == "nt"``.
        inline_limit: Longest script, in bytes, passed inline.

    Yields:
        Argv: The argv to spawn.
    """
    parts = _split_npx_fallback(cmd, windows=windows)
    if parts is None:
        yield list(cmd)
        return
    prefix, script = parts
    encoded = os.fsencode(script)
    if len(encoded) <= inline_limit:
        yield [*prefix, "--call", script]
        return
    fd, path = tempfile.mkstemp(prefix="lintro-npx-", suffix=".sh")
    try:
        with os.fdopen(fd, "wb") as handle:
            # No ``exec``: like npm's inline ``sh -c`` under dash, the shell
            # stays the tool's parent, so a signal-killed tool still exits
            # 128+N with the shell's diagnostic instead of a raw -N.
            handle.write(encoded + b"\n")
        yield [*prefix, "--call", f". {shlex.quote(path)}"]
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
