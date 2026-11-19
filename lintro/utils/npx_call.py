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
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from lintro.utils.npx_install_lock import HeldInstallLock, install_lock
from lintro.utils.npx_integrity import (
    CacheState,
    NpxExit,
    NpxInstallError,
    NpxIntegrityError,
    check_unchanged,
    existing_change,
    verified_cache,
    wait_limit,
)
from lintro.utils.npx_unfinished import BoundCache, bound_cache
from lintro.utils.stop_on_signal import Stoppable, defer_signals

#: npx/bunx flags that take the package spec as the next argument.
PACKAGE_FLAGS: frozenset[str] = frozenset({"-p", "--package"})

#: Prefix of the npm cache option on every npx fallback
#: (:func:`lintro.tools.core.node_fallback.npx_runner_options`).
NPX_CACHE_OPTION: str = "--cache="

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


def npx_cache_option(cmd: Sequence[str]) -> Path | None:
    """Return the npm cache an npx fallback runs from.

    Args:
        cmd: Command about to be spawned.

    Returns:
        The last ``--cache=`` before ``--package`` (npm lets the last one win)
        in lintro's npx fallback shape, or None for any other command.
    """
    index = _package_flag_index(cmd)
    if index is None:
        return None
    caches = [arg for arg in cmd[1:index] if arg.startswith(NPX_CACHE_OPTION)]
    return Path(caches[-1].removeprefix(NPX_CACHE_OPTION)) if caches else None


@contextlib.contextmanager
def npx_shell_safe_command(
    cmd: Sequence[str],
    *,
    windows: bool | None = None,
    inline_limit: int = NPX_INLINE_SCRIPT_LIMIT,
    timeout: float | None = None,
    npx_exit: NpxExit | None = None,
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

    When the command carries a ``--cache``, its contents are verified against
    their lockfile before the argv is yielded, and must be unchanged when the
    context exits normally (:mod:`lintro.utils.npx_integrity`, #3406), on
    every platform. A cache with no entry yet is installed into under lintro's
    exclusive per-pin lock, held until the run has been checked
    (:mod:`lintro.utils.npx_install_lock`, #3421); a warm cache is checked
    under the shared lock and runs without it. Waiting for that lock and for a
    concurrent npx's lock on the cache counts against *timeout*.

    The full check runs after the run only when the context exits normally,
    once npx has exited. An exception from the block (a timeout, a failed
    spawn) is not a result to vouch for, and judging an entry npx was still
    writing would report that as tampering (#3530): on a warm run only the
    entries verified before the spawn are compared again, and a change to
    them is raised in place of the exception, naming it; otherwise the
    exception propagates unchanged. A cold install that npx left unfinished,
    after such an exception or after npx exited with an error, is removed
    under the exclusive lock, from the cache directory recorded before the
    spawn only (:mod:`lintro.utils.npx_unfinished`). After an exception
    raised before npx exited by itself (an *npx_exit* with no status yet),
    every fresh entry without ``package-lock.json`` goes, since npx runs the
    tool only after writing it; after npx exited with an error, only one
    that has no executables either. The caller must have
    stopped and reaped npx before the exception leaves the block.

    The run is registered with :mod:`lintro.utils.stop_on_signal` from
    before the lock wait until the lock is released, so Ctrl-C, ``SIGTERM``
    or ``SIGHUP`` stops it in whichever thread it runs; on the main thread a
    repeated signal during the cleanup is re-raised once it is done.

    Args:
        cmd: Command about to be spawned.
        windows: Whether npx runs on Windows; defaults to ``os.name == "nt"``.
        inline_limit: Longest script, in bytes, passed inline.
        timeout: The tool's timeout in seconds, which bounds that wait.
        npx_exit: Where the caller records how npx exited, before leaving
            the context, so a failed install is told from a tampered one.

    Yields:
        Argv: The argv to spawn.

    Raises:
        BaseException: Whatever ended the block early (a timeout, a failed
            spawn, an interruption), re-raised once the cleanup is done.
        NpxInstallError: npx exited with an error before it finished a cold
            install; the run is incomplete, not refused as tampered.
        NpxIntegrityError: The cache failed its check before the spawn, or
            after the run (also after a warm run that raised), when it is
            marked after_run, or another lintro
            run's first install held the per-pin lock past *timeout*
            (:class:`~lintro.utils.npx_install_lock.NpxInstallLockTimeoutError`),
            or that lock cannot be used or was replaced
            (:class:`~lintro.utils.npx_install_lock.NpxInstallLockError`).
    """
    cache = npx_cache_option(cmd)
    if cache is None:
        with _quoted_argv(cmd, windows=windows, inline_limit=inline_limit) as argv:
            yield argv
        return
    # Registered until the lock is released, so a stop signal reaches this
    # run in whichever thread it runs (:mod:`lintro.utils.stop_on_signal`).
    with Stoppable():
        deadline = time.monotonic() + wait_limit(timeout)
        with install_lock(cache, shared=True, deadline=deadline):
            before = verified_cache(cache, timeout=_remaining(deadline))
        with contextlib.ExitStack() as cold_install:
            installing: _ColdInstall | None = None
            if not before.entries:
                # A cold pin: install it under the exclusive lock, held until the
                # run is checked, so no other lintro run sees npx's half-written
                # entry (#3421). Check again once the lock is held: the run that
                # held it may have installed the pin meanwhile.
                held = cold_install.enter_context(
                    install_lock(cache, shared=False, deadline=deadline),
                )
                before = verified_cache(cache, timeout=_remaining(deadline))
                if before.entries:
                    # Installed while this run waited: run warm, without the lock.
                    cold_install.close()
                else:
                    # Record which cache directory npx installs into, so cleanup
                    # can never be pointed anywhere else, then make the last
                    # check before npx installs: the lock is still ours.
                    bound = cold_install.enter_context(bound_cache(cache))
                    held.check()
                    installing = _ColdInstall(held, bound)
            try:
                with _quoted_argv(
                    cmd,
                    windows=windows,
                    inline_limit=inline_limit,
                ) as argv:
                    yield argv
            except BaseException as exc:
                # npx did not finish: it timed out, could not start, or lintro
                # was interrupted. There is no result to check; the caller
                # reports the failure itself (#3530). A repeated Ctrl-C waits
                # until the cleanup is done and the lock released.
                defer_signals()
                _after_unfinished_run(
                    cache,
                    before,
                    installing,
                    exc,
                    npx_stopped=npx_exit is not None and npx_exit.returncode is None,
                )
                raise
            try:
                check_unchanged(cache, before, timeout=timeout, npx_exit=npx_exit)
            except NpxInstallError:
                if installing is not None:
                    defer_signals()
                    installing.discard_unfinished(before, npx_stopped=False)
                raise
            except NpxIntegrityError as exc:
                exc.after_run = True
                raise


class NpxChangedDuringRunError(NpxIntegrityError):
    """A verified entry changed during a run that also ended early (#3530).

    The tool may have run from the changed entry, so like any change found
    after a run its results cannot be trusted.
    """

    after_run: bool = True


@dataclass(frozen=True)
class _ColdInstall:
    """The exclusive lock and the cache a cold run installs under."""

    lock: HeldInstallLock
    cache: BoundCache | None

    def discard_unfinished(self, before: CacheState, *, npx_stopped: bool) -> None:
        """Remove the entries npx left unfinished, while the lock is ours.

        Only a cold install, under lintro's exclusive per-pin lock, is cleaned
        up: no other lintro run can be installing into the cache then. Only
        the cache directory recorded before the spawn is touched, and only
        entries that show npm never finished
        (:func:`~lintro.utils.npx_integrity.install_unfinished`). Never
        raises, so the exception that ended the run is the one reported.

        Args:
            before: What the cache held before the spawn.
            npx_stopped: Whether lintro stopped npx before it exited by
                itself; then npx never ran the tool from an entry without
                a lockfile, and every such entry goes.
        """
        if self.cache is None:
            return
        try:
            self.lock.check()
        except NpxIntegrityError as exc:
            logger.warning(f"lintro left npx's unfinished install in place: {exc}")
            return
        self.cache.discard_unfinished(keep=before.entries, npx_stopped=npx_stopped)


def _after_unfinished_run(
    cache: Path,
    before: CacheState,
    installing: _ColdInstall | None,
    exc: BaseException,
    *,
    npx_stopped: bool,
) -> None:
    """Clean up, or check what can be checked, after npx did not finish.

    A cold install npx left half-written goes, so the next run, maybe one
    waiting for this lock, starts cold instead of refusing it. On a warm run
    the entries verified before the spawn are compared again, as after a
    finished run: a change to them is reported even though the run also
    failed, since it means the cache cannot be trusted. Entries npx added
    are not judged, being possibly half-written; the next run verifies them.
    An interruption (``KeyboardInterrupt``, ``SystemExit``) is left to
    propagate unchanged.

    Args:
        cache: The ``--cache`` the npx command carried.
        before: What the cache held before the spawn.
        installing: The cold install, or None for a warm run.
        exc: What ended the run.
        npx_stopped: Whether npx had not exited by itself when *exc* was
            raised (no exit status was recorded).

    Raises:
        NpxChangedDuringRunError: An entry verified before a warm run
            changed; it names *exc* too and is chained from it.
    """
    if installing is not None:
        installing.discard_unfinished(before, npx_stopped=npx_stopped)
        return
    if not before.entries or not isinstance(exc, Exception):
        return
    changed = existing_change(Path(os.path.realpath(cache)), before)
    if changed is not None:
        raise NpxChangedDuringRunError(
            f"{changed} The run also ended early: {type(exc).__name__}: {exc}",
        ) from exc


def _remaining(deadline: float) -> float:
    """Return the wait budget left before *deadline*, never zero.

    :func:`~lintro.utils.npx_integrity.wait_limit` reads zero as "no limit",
    so a spent budget is passed as a tiny positive one instead.

    Args:
        deadline: :func:`time.monotonic` time when the budget runs out.

    Returns:
        Seconds left, at least a millisecond.
    """
    return max(deadline - time.monotonic(), 0.001)


@contextlib.contextmanager
def _quoted_argv(
    cmd: Sequence[str],
    *,
    windows: bool | None,
    inline_limit: int,
) -> Iterator[Argv]:
    """Yield the quoted argv for :func:`npx_shell_safe_command`.

    Args:
        cmd: Command about to be spawned.
        windows: Whether npx runs on Windows; None means ``os.name == "nt"``.
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
