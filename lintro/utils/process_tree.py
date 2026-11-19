"""Stop a running child and the processes it started, on POSIX (#3530).

npm runs a package's install scripts as children of npx. When lintro stops
npx itself (the tool timed out, or lintro raised while npx was still
running), killing npx alone would leave those scripts writing into the cache
entry that lintro is about to remove, after it has let go of the install lock.
npx shares lintro's process group, as any tool lintro runs does, so the
terminal's Ctrl-C reaches it directly. lintro therefore cannot kill a group,
and instead finds npx's descendants in the process table and stops each one.

The tree is frozen from the top down: npx gets ``SIGSTOP`` first, then every
process whose parent is already stopped, until a fresh read of the table
finds no new one. A stopped process cannot fork, and cannot reap a child, so
a process whose parent is stopped keeps its process id until lintro has
signalled it: no id that lintro signals can have been reused by an
unrelated process. Then every frozen process is killed, deepest first, with
npx last. lintro never signals its own process or a process group.

Escapes: a descendant whose parent exited before lintro froze it has been
reparented and is no longer found, and the table is read in snapshots, so a
process that forks and exits between two reads can leave a child behind.
When the table cannot be read at all, only npx is killed, which is what
lintro did before #3530, and a debug message says so.

The table comes from ``/proc`` on Linux and from ``ps -A -o pid=,ppid=``
elsewhere.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess  # nosec B404 - runs ps from a fixed path, argv list only
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from loguru import logger

#: Reads the process table as ``{pid: parent pid}``, or None if it cannot.
TableReader = Callable[[], Mapping[int, int] | None]

#: Seconds ``ps`` may take to list the processes.
_PS_SECONDS: float = 5.0

#: Most reads of the table while freezing one tree; each finds a level.
_MAX_READS: int = 64


def _proc_table(proc: Path = Path("/proc")) -> dict[int, int] | None:
    """Read every process and its parent from Linux's ``/proc``.

    Args:
        proc: Where procfs is mounted.

    Returns:
        ``{pid: parent pid}``, or None when ``/proc`` cannot be read.
    """
    try:
        names = os.listdir(proc)
    except OSError:
        return None
    table: dict[int, int] = {}
    for name in names:
        if not name.isdigit():
            continue
        try:
            stat = (proc / name / "stat").read_bytes()
        except OSError:
            continue  # Exited since the listing.
        # ``pid (comm) state ppid ...``; comm may hold spaces and ``)``.
        fields = stat.rpartition(b")")[2].split()
        try:
            table[int(name)] = int(fields[1])
        except (IndexError, ValueError):
            return None
    return table or None


def _ps_table() -> dict[int, int] | None:
    """Read every process and its parent with ``ps``.

    Returns:
        ``{pid: parent pid}``, or None when ``ps`` fails or prints
        something else.
    """
    ps = "/bin/ps" if os.path.exists("/bin/ps") else shutil.which("ps")
    if ps is None:
        return None
    try:
        listed = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [ps, "-A", "-o", "pid=,ppid="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_PS_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    table: dict[int, int] = {}
    for line in listed.stdout.splitlines():
        words = line.split()
        if not words:
            continue
        try:
            pid, parent = (int(word) for word in words)
        except ValueError:
            return None
        table[pid] = parent
    return table or None


def read_process_table() -> dict[int, int] | None:
    """Read every process and its parent.

    Returns:
        ``{pid: parent pid}``, or None when the table cannot be read.
    """
    if sys.platform.startswith("linux"):
        table = _proc_table()
        if table is not None:
            return table
    return _ps_table()


def _signal(pid: int, signum: int) -> None:
    """Send *signum* to *pid*, ignoring one that has already gone.

    Args:
        pid: A verified process id, never a group.
        signum: The signal.
    """
    with contextlib.suppress(OSError):
        os.kill(pid, signum)


def _freeze(root: int, read_table: TableReader) -> tuple[list[int], bool]:
    """Stop *root* and every process below it, from the top down.

    Args:
        root: The unreaped child's process id.
        read_table: What reads the process table.

    Returns:
        The stopped processes, parents before children, and whether the
        table could be read until no new process turned up.
    """
    own = os.getpid()
    _signal(root, signal.SIGSTOP)
    frozen = [root]
    seen = {root}
    for _ in range(_MAX_READS):
        table = read_table()
        if table is None:
            return frozen, False
        # Only a child of a process stopped before this read: it cannot
        # have been reaped since, so its id is still its own.
        found = sorted(
            pid
            for pid, parent in table.items()
            if parent in seen and pid not in seen and pid > 1 and pid != own
        )
        if not found:
            return frozen, True
        for pid in found:
            _signal(pid, signal.SIGSTOP)
            seen.add(pid)
            frozen.append(pid)
    return frozen, False


def kill_tree(
    process: subprocess.Popen[Any],
    *,
    read_table: TableReader = read_process_table,
) -> bool:
    """Kill the running, unreaped *process* and the processes it started.

    The caller has seen ``poll()`` return None and reaps *process* next;
    until then its id cannot be reused.

    Args:
        process: The child, still running.
        read_table: What reads the process table.

    Returns:
        Whether the whole tree was found; False when the table could not
        be read, and *process* alone was sure to be killed.
    """
    frozen, complete = _freeze(process.pid, read_table)
    if not complete:
        logger.debug(
            f"lintro could not read the process table to stop what process "
            f"{process.pid} started; stopping the {len(frozen)} process(es) "
            "it found",
        )
    # Deepest first, so each one's parent still holds its id; npx last.
    for pid in reversed(frozen[1:]):
        _signal(pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()
    return complete
