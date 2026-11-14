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

Node preloads are the other way to replace the pinned tool (#3460). npm exports
its ``node-options`` setting (from any npmrc file or ``npm_config_node_options``
in any letter case) to the tool as ``NODE_OPTIONS``, and an inherited
``NODE_OPTIONS`` reaches both npm and the tool, so a ``--require`` preload could
print ``[]`` and exit 0 in the tool's place. The builder passes an empty
``--node-options=``, which outranks every npmrc file and the environment;
:func:`npx_child_env` handles the environment. It drops every casing of
``npm_config_node_options`` and keeps only the ``NODE_OPTIONS`` entries in
:data:`SAFE_NODE_OPTIONS`, flags that load no code. Unlike the shell rewrite,
this applies on Windows too, where the same variables reach Node.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from loguru import logger

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

#: Environment variable Node reads extra options from.
NODE_OPTIONS_VAR: str = "NODE_OPTIONS"

#: npm's environment spelling of its ``node-options`` setting. npm matches it
#: in any letter case, and the hyphen spelling ``npm_config_node-options`` is
#: dropped too.
NPM_NODE_OPTIONS_VAR: str = "npm_config_node_options"

#: Where the ``NODE_OPTIONS`` policy is documented, for the drop warning.
NODE_OPTIONS_DOCS: str = (
    "docs/configuration.md#nodejs-tool-resolution "
    '("Node preloads cannot replace the tool")'
)

#: Set once the drop warning has been logged; it is logged once per process.
_DROP_WARNED = threading.Event()
_DROP_WARNED_LOCK = threading.Lock()

#: ``NODE_OPTIONS`` entries an npx fallback run keeps: memory limits, warning
#: and diagnostic output, and TLS/DNS choices npm needs to reach the registry.
#: None loads code. Everything else, including ``--require``, ``--import``,
#: ``--loader``, ``--experimental-loader``, ``--env-file``, ``--inspect`` and
#: any flag a later Node adds, is dropped: an allowlist cannot be outflanked by
#: a new code-loading flag the way a denylist can.
SAFE_NODE_OPTIONS: re.Pattern[str] = re.compile(
    r"--max[-_]old[-_]space[-_]size=\d+"
    r"|--max[-_]semi[-_]space[-_]size=\d+"
    r"|--stack[-_]trace[-_]limit=\d+"
    r"|--no-warnings|--no-deprecation|--trace-warnings|--trace-deprecation"
    r"|--trace-uncaught|--enable-source-maps"
    r"|--use-openssl-ca|--use-bundled-ca|--use-system-ca"
    r"|--dns-result-order=(?:ipv4first|ipv6first|verbatim)"
    r"|--disable-warning=[A-Za-z0-9_-]+",
)


def _partition_node_options(value: str) -> tuple[list[str], list[str]]:
    """Split a ``NODE_OPTIONS`` value into kept and dropped entries.

    Node splits ``NODE_OPTIONS`` on spaces and honours double quotes and
    backslash escapes. A value using either is dropped whole rather than
    re-parsed, since no kept flag needs them; an entry joined to another by a
    tab is one entry to Node, matches nothing and is dropped whole too. Extra
    spaces are not entries, so they are never reported as dropped.

    Args:
        value: ``NODE_OPTIONS`` as inherited.

    Returns:
        The entries kept and the entries dropped, each in their original order.
    """
    if '"' in value or "\\" in value:
        return [], [value]
    kept: list[str] = []
    dropped: list[str] = []
    for token in value.split(" "):
        if token:
            (kept if SAFE_NODE_OPTIONS.fullmatch(token) else dropped).append(token)
    return kept, dropped


def filter_node_options(value: str) -> str:
    """Keep only the :data:`SAFE_NODE_OPTIONS` entries of a ``NODE_OPTIONS`` value.

    See :func:`_partition_node_options` for how the value is split.

    Args:
        value: ``NODE_OPTIONS`` as inherited.

    Returns:
        The kept entries joined by single spaces, or ``""``.
    """
    return " ".join(_partition_node_options(value)[0])


def _warn_dropped_once(dropped: Sequence[str]) -> None:
    """Log one warning per process naming the dropped ``NODE_OPTIONS`` entries.

    Args:
        dropped: Entries removed from ``NODE_OPTIONS``.
    """
    with _DROP_WARNED_LOCK:
        if _DROP_WARNED.is_set():
            return
        _DROP_WARNED.set()
    logger.warning(
        f"NODE_OPTIONS entries {', '.join(repr(entry) for entry in dropped)} are "
        "not passed to Node tools run through lintro's npx fallback, because "
        "they may load code in place of the pinned tool; see "
        f"{NODE_OPTIONS_DOCS}",
    )


def _package_flag_index(command: Sequence[str]) -> int | None:
    """Find ``--package`` in lintro's npx fallback shape.

    Args:
        command: Command about to be spawned.

    Returns:
        Index of the package flag when *command* is ``[npx, *--options,
        --package, ...]``, otherwise None.
    """
    if not command or Path(command[0]).name != "npx":
        return None
    for index in range(1, len(command)):
        if command[index] in PACKAGE_FLAGS:
            return index
        if not command[index].startswith("--"):
            # A positional before --package is not the shape lintro builds.
            return None
    return None


def npx_child_env(
    cmd: Sequence[str],
    env: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return the environment to spawn *cmd* with, without Node preloads.

    For lintro's npx fallback the result is a copy of *env* (or of
    ``os.environ`` when *env* is None) without ``npm_config_node_options`` in
    any letter case or hyphen spelling, and with ``NODE_OPTIONS`` reduced by
    :func:`filter_node_options` (removed when nothing is kept; other letter
    casings of it are removed too). Dropped entries are named in one warning
    per process. Every other command, including ``bunx`` and direct binaries,
    gets *env* unchanged.

    Args:
        cmd: Command about to be spawned.
        env: Full environment for the child, or None to inherit.

    Returns:
        The environment to pass to the child, or None to inherit.
    """
    if _package_flag_index(cmd) is None:
        return None if env is None else dict(env)
    source = os.environ if env is None else env
    # Node reads ``NODE_OPTIONS`` case-insensitively on Windows only, where
    # ``os.environ`` already upper-cases keys; other casings are dropped, never
    # promoted.
    child = {
        key: value
        for key, value in source.items()
        if key.casefold() != NODE_OPTIONS_VAR.casefold()
        and key.casefold().replace("-", "_") != NPM_NODE_OPTIONS_VAR
    }
    kept, dropped = _partition_node_options(source.get(NODE_OPTIONS_VAR, ""))
    if dropped:
        _warn_dropped_once(dropped)
    if kept:
        child[NODE_OPTIONS_VAR] = " ".join(kept)
    return child


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
    index = None if windows else _package_flag_index(command)
    if index is None:
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
