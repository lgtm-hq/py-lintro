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

Windows is left alone: there npm uses ``cmd.exe``, which does not glob, and
POSIX quoting would be wrong for it.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Sequence
from pathlib import Path

#: npx flags that take the package spec as the next argument.
_PACKAGE_FLAGS: frozenset[str] = frozenset({"--package", "-p"})


def npx_shell_safe_command(
    cmd: Sequence[str],
    *,
    windows: bool | None = None,
) -> list[str]:
    """Rewrite an npx fallback argv so npm's shell sees every argument literally.

    ``[npx, *options, --package, spec, binary, *args]`` becomes
    ``[npx, *options, --package, spec, --call, "<quoted binary and args>"]``.
    Every other command, including ``bunx`` and direct binaries, is returned
    unchanged.

    Args:
        cmd: Command about to be spawned.
        windows: Whether npx runs on Windows; defaults to ``os.name == "nt"``.

    Returns:
        The argv to spawn.
    """
    command = list(cmd)
    if windows is None:
        windows = os.name == "nt"
    if windows or not command or Path(command[0]).name != "npx":
        return command
    for index in range(1, len(command)):
        if command[index] in _PACKAGE_FLAGS:
            break
        if not command[index].startswith("--"):
            # A positional before --package is not the shape lintro builds.
            return command
    else:
        return command
    script_start = index + 2
    if script_start >= len(command):
        return command
    return [
        *command[:script_start],
        "--call",
        shlex.join(command[script_start:]),
    ]
