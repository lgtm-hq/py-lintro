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
finds no new one or :data:`_FREEZE_SECONDS` have passed. Then every frozen
process is killed, deepest first, with npx last. lintro never signals its own
process or a process group.

A process id read from the table is not trusted on its own: a stopped parent
that ignores ``SIGCHLD`` has its exited children reaped by the kernel, and
the freed id can then be given to an unrelated process. Each process is
therefore recorded with its parent and its start time, and immediately
before each ``SIGSTOP`` and each ``SIGKILL`` lintro reads it, and every
recorded ancestor up to npx, again: the signal is sent only if they all
still match. On Linux the process is opened as a pidfd before that check and
signalled through it, so the check and the signal name the same process.
Elsewhere a short window between the check and the signal remains. A
process whose start time cannot be read is not signalled at all; npx, which
lintro has not reaped, always is.

Escapes: a descendant whose parent exited before lintro froze it (one that
double-forks or daemonises) has been reparented and is not found, and the
table is read in snapshots, so a process that forks and exits between two
reads can leave a child behind. When the table cannot be read at all, only
npx is killed, which is what lintro did before #3530. Whenever the stop is
incomplete, a debug message says why and :func:`kill_tree` returns False.

The table comes from ``/proc`` on Linux, from ``libproc`` on macOS, and from
``ps -A -o pid=,ppid=,lstart=`` elsewhere.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import functools
import os
import shutil
import signal
import struct
import subprocess  # nosec B404 - runs ps from a fixed path, argv list only
import sys
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

from loguru import logger

#: Seconds ``ps`` may take to list the processes.
_PS_SECONDS: float = 5.0

#: Seconds spent finding and stopping one tree before lintro gives up on
#: what it has not found yet.
_FREEZE_SECONDS: float = 5.0

#: ``/proc/<pid>/stat`` fields after the command name: state is field 3.
_STAT_PARENT: int = 1
_STAT_STARTED: int = 19  # Field 22, start time in clock ticks after boot.


@dataclass(frozen=True)
class Identity:
    """Who a process is: its parent, and when it started.

    Attributes:
        parent: The parent's process id.
        started: The start time as the platform reports it, or None when it
            could not be read; then the process cannot be told from a later
            one with the same id.
    """

    parent: int
    started: str | None


#: Reads every process as ``{pid: identity}``, or None if it cannot.
ListReader = Callable[[], Mapping[int, Identity] | None]

#: Reads the given processes as ``{pid: identity}``, leaving out the ones
#: that have gone, or None if it cannot read them.
PidReader = Callable[[Collection[int]], Mapping[int, Identity] | None]


@dataclass(frozen=True)
class ProcessTable:
    """Where process identities come from; both readers share one format.

    Attributes:
        read_all: Lists every process.
        read: Reads chosen processes again.
        pidfd: Whether to signal through a pidfd (Linux).
    """

    read_all: ListReader
    read: PidReader
    pidfd: bool = False


def _proc_identity(proc: Path, pid: int) -> Identity | None:
    """Read one process from Linux's ``/proc``.

    Args:
        proc: Where procfs is mounted.
        pid: The process.

    A stat line not in the expected format raises ``ValueError`` or
    ``IndexError``.

    Returns:
        Its identity, or None when it has gone.
    """
    try:
        stat = (proc / str(pid) / "stat").read_bytes()
    except OSError:
        return None
    # ``pid (comm) state ppid ...``; comm may hold spaces and ``)``.
    fields = stat.rpartition(b")")[2].split()
    parent = int(fields[_STAT_PARENT])
    started = fields[_STAT_STARTED] if len(fields) > _STAT_STARTED else b""
    return Identity(parent, started.decode() if started.isdigit() else None)


def _proc_list(proc: Path = Path("/proc")) -> dict[int, Identity] | None:
    """Read every process from Linux's ``/proc``.

    Args:
        proc: Where procfs is mounted.

    Returns:
        ``{pid: identity}``, or None when ``/proc`` cannot be read.
    """
    try:
        names = os.listdir(proc)
    except OSError:
        return None
    return _proc_read([int(name) for name in names if name.isdigit()], proc) or None


def _proc_read(
    pids: Collection[int],
    proc: Path = Path("/proc"),
) -> dict[int, Identity] | None:
    """Read the given processes from Linux's ``/proc``.

    Args:
        pids: The processes.
        proc: Where procfs is mounted.

    Returns:
        ``{pid: identity}`` for those still there, or None when a stat line
        cannot be parsed.
    """
    table: dict[int, Identity] = {}
    for pid in pids:
        try:
            identity = _proc_identity(proc, pid)
        except (IndexError, ValueError):
            return None
        if identity is not None:
            table[pid] = identity
    return table


#: ``proc_pidinfo`` flavours and the sizes of what they fill in.
_PIDTBSDINFO, _PIDTBSDINFO_SIZE = 3, 136
_PIDT_SHORTBSDINFO, _PIDT_SHORTBSDINFO_SIZE = 13, 64

#: ``struct proc_bsdinfo`` up to the start time; ppid is field 4.
_BSDINFO = struct.Struct("=12I16s32s6I2Q")


@functools.cache
def _libproc() -> Any | None:
    """Load macOS's ``libproc``.

    Returns:
        The library, or None when it cannot be loaded.
    """
    path = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
    try:
        lib = ctypes.CDLL(path, use_errno=True)
        lib.proc_listallpids.restype = ctypes.c_int
        lib.proc_pidinfo.restype = ctypes.c_int
        lib.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
    except (OSError, AttributeError):
        return None
    return lib


def _libproc_identity(lib: Any, pid: int) -> Identity | None:
    """Read one process through ``proc_pidinfo``.

    Another user's process only gives its parent, without a start time.

    Args:
        lib: ``libproc``.
        pid: The process.

    Returns:
        Its identity, or None when it has gone or cannot be read.
    """
    info = ctypes.create_string_buffer(_PIDTBSDINFO_SIZE)
    if lib.proc_pidinfo(pid, _PIDTBSDINFO, 0, info, _PIDTBSDINFO_SIZE) == len(info):
        fields = _BSDINFO.unpack_from(info.raw)
        return Identity(fields[4], f"{fields[-2]}.{fields[-1]:06d}")
    short = ctypes.create_string_buffer(_PIDT_SHORTBSDINFO_SIZE)
    size = _PIDT_SHORTBSDINFO_SIZE
    if lib.proc_pidinfo(pid, _PIDT_SHORTBSDINFO, 0, short, size) == len(short):
        return Identity(struct.unpack_from("=2I", short.raw)[1], None)
    return None


def _libproc_read(pids: Collection[int]) -> dict[int, Identity] | None:
    """Read the given processes through ``libproc``.

    Args:
        pids: The processes.

    Returns:
        ``{pid: identity}`` for those still there, or None without
        ``libproc``.
    """
    lib = _libproc()
    if lib is None:
        return None
    table: dict[int, Identity] = {}
    for pid in pids:
        identity = _libproc_identity(lib, pid)
        if identity is not None:
            table[pid] = identity
    return table


def _libproc_list() -> dict[int, Identity] | None:
    """Read every process through ``libproc``.

    Returns:
        ``{pid: identity}``, or None when the list cannot be read.
    """
    lib = _libproc()
    if lib is None:
        return None
    count = lib.proc_listallpids(None, 0)
    if count <= 0:
        return None
    buffer = (ctypes.c_int * (count + 256))()
    listed = lib.proc_listallpids(buffer, ctypes.sizeof(buffer))
    if listed <= 0:
        return None
    return _libproc_read([pid for pid in buffer[:listed] if pid > 0]) or None


def _ps(*selection: str) -> dict[int, Identity] | None:
    """Read processes with ``ps``, the start time as ``lstart`` prints it.

    Args:
        *selection: ``-A``, or ``-p`` and a comma-separated list.

    Returns:
        ``{pid: identity}``, or None when ``ps`` fails or prints something
        else. Asked for processes that have all gone, BSD ``ps`` fails
        without output: that is an empty table.
    """
    ps = "/bin/ps" if os.path.exists("/bin/ps") else shutil.which("ps")
    if ps is None:
        return None
    try:
        listed = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [ps, *selection, "-o", "pid=,ppid=,lstart="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_PS_SECONDS,
            check=False,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return {} if selection[0] == "-p" and not listed.stdout.strip() else None
    table: dict[int, Identity] = {}
    for line in listed.stdout.splitlines():
        words = line.split()
        if not words:
            continue
        try:
            pid, parent = int(words[0]), int(words[1])
        except (IndexError, ValueError):
            return None
        table[pid] = Identity(parent, " ".join(words[2:]) or None)
    return table


def _ps_list() -> dict[int, Identity] | None:
    """Read every process with ``ps``.

    Returns:
        ``{pid: identity}``, or None when ``ps`` cannot list them.
    """
    return _ps("-A") or None


def _ps_read(pids: Collection[int]) -> dict[int, Identity] | None:
    """Read the given processes with one ``ps`` call.

    Args:
        pids: The processes.

    Returns:
        ``{pid: identity}`` for those still there, or None on failure.
    """
    if not pids:
        return {}
    return _ps("-p", ",".join(str(pid) for pid in pids))


def system_table() -> ProcessTable | None:
    """Pick this system's source of process identities.

    Returns:
        The table, or None when no source can be read.
    """
    if sys.platform.startswith("linux") and os.path.exists("/proc/self/stat"):
        return ProcessTable(
            read_all=_proc_list,
            read=_proc_read,
            pidfd=getattr(os, "pidfd_open", None) is not None,
        )
    if sys.platform == "darwin" and _libproc() is not None:
        return ProcessTable(read_all=_libproc_list, read=_libproc_read)
    if os.path.exists("/bin/ps") or shutil.which("ps") is not None:
        return ProcessTable(read_all=_ps_list, read=_ps_read)
    return None


class _Sent(StrEnum):
    """What came of a signal to a recorded process."""

    SENT = auto()
    GONE = auto()
    CHANGED = auto()
    UNREADABLE = auto()


def _open_pidfd(pid: int) -> int | None:
    """Open *pid* as a pidfd, so a later signal cannot reach a successor.

    Args:
        pid: The process.

    Returns:
        The descriptor, or None when pidfds are unavailable.

    Raises:
        ProcessLookupError: No process has *pid*.
    """
    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is None:
        return None
    try:
        fd: int = pidfd_open(pid)
    except ProcessLookupError:
        raise
    except OSError:
        return None
    return fd


@dataclass
class _Tree:
    """The tree under one unreaped child, as found so far."""

    root: int
    table: ProcessTable
    known: dict[int, Identity]

    def chain(self, pid: int) -> list[int]:
        """Return *pid* and its recorded ancestors below the root.

        Args:
            pid: A recorded process.

        Returns:
            The process ids, *pid* first.
        """
        chain = [pid]
        while self.known[chain[-1]].parent != self.root:
            chain.append(self.known[chain[-1]].parent)
        return chain

    def check(self, chain: list[int]) -> _Sent:
        """Compare *chain* as it is now with what was recorded.

        Args:
            chain: From :meth:`chain`.

        Returns:
            SENT when every process still matches, as a go-ahead.
        """
        now = self.table.read(chain)
        if now is None:
            return _Sent.UNREADABLE
        if chain[0] not in now:
            return _Sent.GONE
        if any(now.get(pid) != self.known[pid] for pid in chain):
            return _Sent.CHANGED
        return _Sent.SENT

    def signal(self, pid: int, signum: int) -> _Sent:
        """Send *signum* to *pid* only if it is still the recorded process.

        Args:
            pid: A recorded process, never the root.
            signum: The signal.

        Returns:
            Whether it was sent, and if not, why.
        """
        try:
            pidfd = _open_pidfd(pid) if self.table.pidfd else None
        except ProcessLookupError:
            return _Sent.GONE
        try:
            verdict = self.check(self.chain(pid))
            if verdict is not _Sent.SENT:
                return verdict
            send = getattr(signal, "pidfd_send_signal", None)
            try:
                if pidfd is not None and send is not None:
                    send(pidfd, signum)
                else:
                    os.kill(pid, signum)
            except ProcessLookupError:
                return _Sent.GONE
            except OSError:
                return _Sent.UNREADABLE
            return _Sent.SENT
        finally:
            if pidfd is not None:
                os.close(pidfd)


def _signal_root(pid: int, signum: int) -> None:
    """Send *signum* to the unreaped child, whose id is its own.

    Args:
        pid: The child.
        signum: The signal.
    """
    with contextlib.suppress(OSError):
        os.kill(pid, signum)


def _skipped(
    pid: int,
    why: _Sent,
    problems: list[str],
    *,
    stopped: bool = False,
) -> None:
    """Note a process that was not signalled, if that leaves the stop short.

    One that has gone needs nothing. One found under a stale entry of the
    table is not lintro's to stop; but one lintro already stopped that no
    longer matches may be left stopped, so that counts, as does any
    process whose identity could not be read.

    Args:
        pid: The process.
        why: Why it was not signalled.
        problems: Where the reasons for an incomplete stop go.
        stopped: Whether lintro had already stopped it.
    """
    if why is _Sent.UNREADABLE or (stopped and why is _Sent.CHANGED):
        problems.append(
            f"lintro could not confirm that process {pid} is still the one it "
            "found, so it did not signal it",
        )


def _freeze(tree: _Tree, problems: list[str]) -> list[int]:
    """Stop the root and every verified process below it, from the top down.

    Args:
        tree: The tree to stop; its recorded processes grow.
        problems: Where the reasons for an incomplete stop go.

    Returns:
        The stopped processes, parents before children, the root first.
    """
    own = os.getpid()
    give_up = time.monotonic() + _FREEZE_SECONDS
    _signal_root(tree.root, signal.SIGSTOP)
    frozen = [tree.root]
    judged: set[tuple[int, Identity]] = set()
    while True:
        if time.monotonic() >= give_up:
            problems.append(
                f"lintro was still finding processes that process {tree.root} "
                f"started after {_FREEZE_SECONDS:.0f}s, so some may still be "
                "running",
            )
            return frozen
        listing = tree.table.read_all()
        if listing is None:
            problems.append("lintro could not read the process table")
            return frozen
        parents = set(frozen)
        found = sorted(
            (
                (pid, identity)
                for pid, identity in listing.items()
                if identity.parent in parents
                and pid not in parents
                and pid > 1
                and pid != own
                and (pid, identity) not in judged
            ),
            key=lambda item: item[0],
        )
        if not found:
            return frozen
        for pid, identity in found:
            judged.add((pid, identity))
            if identity.started is None:
                _skipped(pid, _Sent.UNREADABLE, problems)
                continue
            tree.known[pid] = identity
            sent = tree.signal(pid, signal.SIGSTOP)
            if sent is _Sent.SENT:
                frozen.append(pid)
            else:
                del tree.known[pid]
                _skipped(pid, sent, problems)


def kill_tree(
    process: subprocess.Popen[Any],
    *,
    table: ProcessTable | None = None,
) -> bool:
    """Kill the running, unreaped *process* and the processes it started.

    The caller has seen ``poll()`` return None and reaps *process* next;
    until then its id cannot be reused.

    Args:
        process: The child, still running.
        table: Where process identities come from; this system's by default.

    Returns:
        Whether the whole tree was found and killed; False when the table
        could not be read, a process could not be verified, or the search
        ran out of time, and only *process* was sure to be killed.
    """
    problems: list[str] = []
    source = table if table is not None else system_table()
    frozen = [process.pid]
    if source is None:
        problems.append("lintro could not read the process table")
        _signal_root(process.pid, signal.SIGSTOP)
    else:
        tree = _Tree(process.pid, source, {})
        frozen = _freeze(tree, problems)
        # Deepest first, so each one's parent still holds its id; npx last.
        for pid in reversed(frozen[1:]):
            sent = tree.signal(pid, signal.SIGKILL)
            _skipped(pid, sent, problems, stopped=True)
    with contextlib.suppress(OSError):
        process.kill()
    for problem in problems:
        logger.debug(
            f"{problem}; stopping what process {process.pid} started is "
            f"incomplete: {len(frozen) - 1} descendant(s) stopped",
        )
    return not problems
