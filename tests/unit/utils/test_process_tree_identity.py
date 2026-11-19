"""lintro signals a descendant of npx only while it is the process it found (#3530).

Each test runs a real process tree and a real process table; only the
listing lintro starts from is doctored, the way a delayed snapshot or a
reused process id would make it wrong. Whatever lintro then signals is
observed on the real processes.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - spawns test children, argv lists only
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.utils import process_tree
from lintro.utils.process_tree import Identity, ProcessTable, kill_tree, system_table

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="process trees are stopped by pid on POSIX only",
)


def _system() -> ProcessTable:
    """Return this system's process table, which every POSIX test host has.

    Returns:
        The table.
    """
    table = system_table()
    assert table is not None  # narrow type for mypy
    return table


def _doctored(
    system: ProcessTable,
    change: Callable[[dict[int, Identity]], None],
) -> ProcessTable:
    """Return *system* with every listing passed through *change*.

    Args:
        system: The real table.
        change: Edits a fresh listing in place.

    Returns:
        A table that lists what *change* leaves and re-reads the real one.
    """

    def listing() -> Mapping[int, Identity] | None:
        table = dict(system.read_all() or {})
        change(table)
        return table

    return ProcessTable(read_all=listing, read=system.read, pidfd=system.pidfd)


def _read_ids(path: Path, budget: float = 20.0) -> dict[str, int]:
    """Wait for a child to record ``name=pid`` words, then return them.

    Args:
        path: The file, renamed into place once written.
        budget: Longest wait in seconds.

    Returns:
        The ids by name.
    """
    give_up = time.monotonic() + budget
    while not path.exists():
        assert_that(time.monotonic()).is_less_than(give_up)
        time.sleep(0.02)
    words = path.read_text(encoding="utf-8").split()
    return {name: int(pid) for name, pid in (word.split("=") for word in words)}


def _gone(pid: int, budget: float = 10.0) -> bool:
    """Report whether no process has *pid* within *budget* seconds.

    Args:
        pid: The process id.
        budget: Longest wait in seconds.

    Returns:
        True once it has gone.
    """
    give_up = time.monotonic() + budget
    while time.monotonic() < give_up:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _running(pid: int) -> bool:
    """Report whether *pid* exists and is neither stopped nor a zombie.

    Args:
        pid: The process id.

    Returns:
        True for a live process that lintro did not stop.
    """
    state = subprocess.run(  # nosec B603 B607 - fixed argv
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and state[0] not in "TZ"


def _kill_quietly(*pids: int) -> None:
    """Kill leftovers of a test.

    Args:
        *pids: Process ids to kill if they still exist.
    """
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _sleeper() -> subprocess.Popen[str]:
    """Start a process unrelated to the tree, as one given a reused id.

    Returns:
        The running process.
    """
    return subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )


#: npx with ``SIGCHLD`` ignored: the kernel reaps its children itself, even
#: while it is stopped. Its child exits once told to.
_IGNORING = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGCHLD, signal.SIG_IGN)\n"
    "ids, go = sys.argv[1], sys.argv[2]\n"
    "waiter = ('import os, sys, time\\n'"
    " 'while not os.path.exists(sys.argv[1]):\\n'"
    " '    time.sleep(0.02)\\n')\n"
    "child = subprocess.Popen([sys.executable, '-c', waiter, go])\n"
    "with open(ids + '.tmp', 'w') as out:\n"
    "    out.write(f'child={child.pid}')\n"
    "os.rename(ids + '.tmp', ids)\n"
    "time.sleep(60)\n"
)


def test_an_id_freed_by_a_reaped_child_and_reused_is_not_signalled(
    tmp_path: Path,
) -> None:
    """A stopped npx ignoring SIGCHLD loses its child; the impostor survives.

    The listing lintro reads was taken while the child lived. By the time
    lintro acts the kernel has reaped the child, and the listing names
    another process under the freed id, with the child's parent and start.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    ids, go = tmp_path / "ids", tmp_path / "go"
    npx = subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", _IGNORING, str(ids), str(go)],
        text=True,
    )
    impostor = _sleeper()
    child = 0
    try:
        child = _read_ids(ids)["child"]
        system = _system()
        record = (system.read([child]) or {})[child]
        assert_that(record.parent).is_equal_to(npx.pid)
        os.kill(npx.pid, signal.SIGSTOP)
        go.touch()
        assert_that(_gone(child)).is_true()  # Reaped while npx is stopped.

        def reused(table: dict[int, Identity]) -> None:
            table[impostor.pid] = record

        kill_tree(npx, table=_doctored(system, reused))
        npx.wait()

        assert_that(_running(impostor.pid)).is_true()
    finally:
        _kill_quietly(npx.pid, impostor.pid, child)
        impostor.wait()
        npx.wait()


#: npx with a child that runs until it is killed.
_PARENT = (
    "import os, subprocess, sys, time\n"
    "ids = sys.argv[1]\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "with open(ids + '.tmp', 'w') as out:\n"
    "    out.write(f'child={child.pid}')\n"
    "os.rename(ids + '.tmp', ids)\n"
    "time.sleep(60)\n"
)


def _start_npx(tmp_path: Path) -> tuple[subprocess.Popen[str], int]:
    """Start :data:`_PARENT` as npx is started, and read its child's id.

    Args:
        tmp_path: Where it records the id.

    Returns:
        npx and its child's id.
    """
    npx = subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", _PARENT, str(tmp_path / "ids")],
        text=True,
    )
    return npx, _read_ids(tmp_path / "ids")["child"]


def test_a_descendant_whose_start_time_differs_is_not_signalled(
    tmp_path: Path,
) -> None:
    """Same id and parent but another start time: not the process listed.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    npx, child = _start_npx(tmp_path)
    try:

        def restarted(table: dict[int, Identity]) -> None:
            listed = table[child]
            table[child] = Identity(listed.parent, f"{listed.started}0")

        kill_tree(npx, table=_doctored(_system(), restarted))
        npx.wait()

        assert_that(_running(child)).is_true()
    finally:
        _kill_quietly(npx.pid, child)
        npx.wait()


def _no_start_time(system: ProcessTable, child: int) -> ProcessTable:
    """Return *system* listing *child* without its start time.

    Args:
        system: The real table.
        child: The process.

    Returns:
        The table.
    """

    def unknown(table: dict[int, Identity]) -> None:
        table[child] = Identity(table[child].parent, None)

    return _doctored(system, unknown)


def _unreadable_again(system: ProcessTable, child: int) -> ProcessTable:
    """Return *system* that lists *child* but cannot read it again.

    Args:
        system: The real table.
        child: The process.

    Returns:
        The table.
    """
    del child
    return ProcessTable(read_all=system.read_all, read=lambda _: None)


#: Ways lintro can fail to establish who a listed process is.
_UNREADABLE: dict[str, Callable[[ProcessTable, int], ProcessTable]] = {
    "listed-without-a-start-time": _no_start_time,
    "cannot-be-read-before-the-signal": _unreadable_again,
}


@pytest.mark.parametrize("case", list(_UNREADABLE), ids=list(_UNREADABLE))
def test_a_descendant_lintro_cannot_identify_is_left_alone(
    tmp_path: Path,
    case: str,
) -> None:
    """Only npx is killed, as before #3530, and the stop is not complete.

    Args:
        tmp_path: Temporary directory provided by pytest.
        case: How the identity is unreadable.
    """
    messages: list[str] = []
    sink = logger.add(messages.append, level="DEBUG", format="{message}")
    npx, child = _start_npx(tmp_path)
    try:
        complete = kill_tree(npx, table=_UNREADABLE[case](_system(), child))
        npx.wait()

        assert_that(complete).is_false()
        assert_that(npx.returncode).is_equal_to(-signal.SIGKILL)
        assert_that(_running(child)).is_true()
        assert_that("".join(messages)).contains(
            f"could not confirm that process {child}",
        )
    finally:
        logger.remove(sink)
        _kill_quietly(npx.pid, child)
        npx.wait()


#: A chain of shells, each the parent of the next, every one alive; the
#: deepest keeps appending to a log.
_CHAIN = (
    'n=$1; log=$2\nif [ "$n" -gt 0 ]; then\n  sh "$0" $((n - 1)) "$log" &\n'
    '  wait\nelse\n  echo $$ > "$log.pid.tmp"; mv "$log.pid.tmp" "$log.pid"\n'
    '  while :; do echo x >> "$log"; sleep 0.02; done\nfi\n'
)


def test_a_chain_deeper_than_any_read_count_is_stopped_to_the_bottom(
    tmp_path: Path,
) -> None:
    """Each read finds one level; lintro keeps reading until none is new.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    script, log = tmp_path / "chain.sh", tmp_path / "chain.log"
    script.write_text(_CHAIN, encoding="utf-8")
    npx = subprocess.Popen(["sh", str(script), "80", str(log)])  # nosec B603 B607
    deepest = 0
    try:
        deepest = _read_ids_file(Path(f"{log}.pid"))
        complete = kill_tree(npx)
        npx.wait()

        assert_that(complete).is_true()
        assert_that(_gone(deepest)).is_true()
    finally:
        _kill_quietly(npx.pid, deepest)
        npx.wait()


def _read_ids_file(path: Path, budget: float = 30.0) -> int:
    """Wait for a process id written to *path*.

    Args:
        path: The file, renamed into place once written.
        budget: Longest wait in seconds.

    Returns:
        The id.
    """
    give_up = time.monotonic() + budget
    while not path.exists():
        assert_that(time.monotonic()).is_less_than(give_up)
        time.sleep(0.02)
    return int(path.read_text(encoding="utf-8"))


def test_a_search_that_never_settles_ends_and_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listing that names a new process on every read cannot hold lintro.

    lintro stops at its time budget, kills npx, and logs that the search
    ran out of time, not that the table could not be read.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Shortens the budget.
    """
    monkeypatch.setattr(process_tree, "_FREEZE_SECONDS", 0.5)
    messages: list[str] = []
    sink = logger.add(messages.append, level="DEBUG", format="{message}")
    npx, child = _start_npx(tmp_path)
    reads: list[int] = []

    def growing(table: dict[int, Identity]) -> None:
        reads.append(len(reads))
        table[child] = Identity(npx.pid, f"new-{len(reads)}")

    try:
        began = time.monotonic()
        complete = kill_tree(npx, table=_doctored(_system(), growing))
        npx.wait()

        assert_that(time.monotonic() - began).is_less_than(5.0)
        assert_that(complete).is_false()
        assert_that(len(reads)).is_greater_than(1)
        text = "".join(messages)
        assert_that(text).contains("still finding processes")
        assert_that(text).does_not_contain("could not read the process table")
    finally:
        logger.remove(sink)
        _kill_quietly(npx.pid, child)
        npx.wait()


def test_linux_stat_lines_give_the_parent_and_start_time(tmp_path: Path) -> None:
    """Field 4 is the parent and field 22 the start, past any command name.

    A command name may hold spaces and ``)``; a line cut short has no start
    time, so that process cannot be signalled.

    Args:
        tmp_path: Stands in for ``/proc``.
    """
    rest = " ".join(str(field) for field in range(5, 22))
    lines = {
        "40": f"40 (np) x) S 7 {rest} 123456 99 98\n",
        "41": "41 (sh) S 40 1 1\n",
        "self": "not a process\n",
    }
    for name, line in lines.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "stat").write_text(line, encoding="utf-8")

    listed = process_tree._proc_list(tmp_path)

    assert_that(listed).is_equal_to(
        {40: Identity(7, "123456"), 41: Identity(40, None)},
    )
    assert_that(process_tree._proc_read([40, 42], tmp_path)).is_equal_to(
        {40: Identity(7, "123456")},
    )
