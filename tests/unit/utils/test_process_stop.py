"""Stopping a child process before lintro cleans up after it (#3530)."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess  # nosec B404 - spawns a sleeping child, argv list only
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.utils import process_stop
from lintro.utils.process_stop import ChildStillRunningError, run_reaped, stop_process
from lintro.utils.process_tree import Identity, ProcessTable, system_table

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the child is a POSIX sleep; Windows is exercised through windows=True",
)


def _system() -> ProcessTable:
    """Return this system's process table, which every POSIX test host has.

    Returns:
        The table.
    """
    table = system_table()
    assert table is not None  # narrow type for mypy
    return table


def _sleeper() -> subprocess.Popen[str]:
    """Start a child that runs until it is killed.

    Returns:
        The running child.
    """
    return subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )


def test_on_windows_the_whole_tree_is_ended_before_the_child_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``npx.cmd``'s node child goes too: ``taskkill /T`` ends the tree.

    Args:
        monkeypatch: Fixes ``SYSTEMROOT`` so the taskkill path is known.
    """
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    ran: list[list[str]] = []
    child = _sleeper()

    stop_process(child, windows=True, run=lambda argv, **_: ran.append(argv))

    assert_that(ran).is_length(1)
    assert_that(ran[0][0]).ends_with("taskkill.exe")
    assert_that(ran[0][1:]).is_equal_to(["/F", "/T", "/PID", str(child.pid)])
    assert_that(child.returncode).is_not_none()


def test_a_child_that_already_exited_is_not_signalled() -> None:
    """Its process id may belong to something else by now: nothing is run."""
    child = subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", "pass"],
        text=True,
    )
    child.wait()
    ran: list[list[str]] = []

    stop_process(child, windows=True, run=lambda argv, **_: ran.append(argv))

    assert_that(ran).is_empty()


def test_a_timeout_reaps_the_child_and_keeps_its_output(tmp_path: Path) -> None:
    """The child has exited when the timeout is raised, with what it printed.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    pid_file = tmp_path / "pid"
    script = (
        "import os, sys, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_reaped([sys.executable, "-c", script], timeout=5.0)

    output = caught.value.output
    text = output.decode() if isinstance(output, bytes) else output
    assert_that(text).contains("started")
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _gone(pid: int, budget: float = 10.0) -> bool:
    """Report whether *pid* has exited and been reaped within *budget*.

    A killed grandchild is reaped by init, not by the test, so it is polled.

    Args:
        pid: The process id.
        budget: Longest wait in seconds.

    Returns:
        True once no process has *pid*.
    """
    give_up = time.monotonic() + budget
    while time.monotonic() < give_up:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _read_pids(path: Path, budget: float = 20.0) -> dict[str, int]:
    """Wait for a child to record its process ids, then return them.

    Args:
        path: The file the child writes ``name=pid`` words to, then renames.
        budget: Longest wait in seconds.

    Returns:
        The recorded ids by name.
    """
    give_up = time.monotonic() + budget
    while not path.exists():
        assert_that(time.monotonic()).is_less_than(give_up)
        time.sleep(0.02)
    words = path.read_text(encoding="utf-8").split()
    return {name: int(pid) for name, pid in (word.split("=") for word in words)}


#: A child that starts a grandchild appending to a log, the way npm's
#: install scripts keep writing under npx, records the ids, then runs on.
#: ``{then}`` runs once the ids are recorded.
_PARENT = (
    "import os, subprocess, sys, time\n"
    "log, ids = sys.argv[1], sys.argv[2]\n"
    "writer = ('import sys, time\\n'"
    " 'while True:\\n'"
    ' \'    open(sys.argv[1], "a").write("x")\\n\''
    " '    time.sleep(0.02)\\n')\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', writer, log])\n"
    "with open(ids + '.tmp', 'w') as out:\n"
    "    out.write(f'child={{os.getpid()}} grandchild={{grandchild.pid}} '\n"
    "              f'group={{os.getpgid(0)}} session={{os.getsid(0)}}')\n"
    "os.rename(ids + '.tmp', ids)\n"
    "{then}"
    "time.sleep(60)\n"
)


def _stays_still(log: Path) -> bool:
    """Report whether nothing appends to *log* any more.

    Args:
        log: The grandchild's log.

    Returns:
        True when its size does not change over half a second.
    """
    before = log.stat().st_size if log.exists() else 0
    time.sleep(0.5)
    return (log.stat().st_size if log.exists() else 0) == before


def _kill_quietly(*pids: int) -> None:
    """Kill leftovers of a failed test.

    Args:
        *pids: Process ids to kill if they still exist.
    """
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _survivors(marker: str, budget: float = 10.0) -> list[str]:
    """Return the live processes whose command line holds *marker*.

    Zombies waiting for init to reap them are not counted: they run nothing.

    Args:
        marker: Text only the test's own processes carry, such as a path.
        budget: How long to wait for them to go.

    Returns:
        Their ``ps`` lines; empty once none is left.
    """
    give_up = time.monotonic() + budget
    while True:
        listed = subprocess.run(  # nosec B603 B607 - fixed argv
            ["ps", "-A", "-o", "pid=,stat=,command="],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        alive = [
            line
            for line in listed
            if marker in line and "Z" not in line.split(maxsplit=2)[1]
        ]
        if not alive or time.monotonic() >= give_up:
            return alive
        time.sleep(0.05)


def _start_parent(tmp_path: Path, then: str = "") -> subprocess.Popen[str]:
    """Start :data:`_PARENT` directly, the way npx is started.

    Args:
        tmp_path: Where its log and ids go.
        then: What it runs once the ids are recorded.

    Returns:
        The running child.
    """
    return subprocess.Popen(  # nosec B603 - fixed argv
        [
            sys.executable,
            "-c",
            _PARENT.format(then=then),
            str(tmp_path / "log"),
            str(tmp_path / "ids"),
        ],
        text=True,
    )


def test_a_timeout_stops_what_the_child_started_too(tmp_path: Path) -> None:
    """The install scripts npm runs under npx go when npx is stopped.

    The child runs in lintro's own session and group, as on ``main``, so
    the terminal's Ctrl-C still reaches it; lintro finds its grandchild in
    the process table instead.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    log, ids = tmp_path / "log", tmp_path / "ids"
    pids: dict[str, int] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_reaped(
                [sys.executable, "-c", _PARENT.format(then=""), str(log), str(ids)],
                timeout=3.0,
            )
        pids = _read_pids(ids)
        assert_that(pids["group"]).is_equal_to(os.getpgid(0))
        assert_that(pids["session"]).is_equal_to(os.getsid(0))
        assert_that(_gone(pids["grandchild"])).is_true()
        assert_that(_stays_still(log)).is_true()
    finally:
        _kill_quietly(*(pid for name, pid in pids.items() if "child" in name))


#: A child whose grandchild keeps starting processes that append to a log
#: and stay, the way a busy install script does; the log's path is in each
#: one's command line.
_FORKER = (
    "import subprocess, sys, time\n"
    "log = sys.argv[1]\n"
    'loop = (\'while :; do sh -c "echo x >> $0; exec sleep 30" "$0" & \''
    " 'sleep 0.01; done')\n"
    "subprocess.Popen(['sh', '-c', loop, log])\n"
    "time.sleep(60)\n"
)


def test_a_descendant_that_keeps_forking_is_stopped_with_all_it_started(
    tmp_path: Path,
) -> None:
    """The tree is frozen from the top, so nothing new escapes the stop.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    log = tmp_path / "forker.log"
    child = subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", _FORKER, str(log)],
        text=True,
    )
    try:
        give_up = time.monotonic() + 20.0
        while not log.exists() or log.stat().st_size < 20:
            assert_that(time.monotonic()).is_less_than(give_up)
            time.sleep(0.02)

        stop_process(child, tree=True)

        assert_that(child.returncode).is_not_none()
        assert_that(_survivors(str(log))).is_empty()
        assert_that(_stays_still(log)).is_true()
    finally:
        child.kill()
        child.wait()


def test_without_a_process_table_only_the_child_is_stopped(tmp_path: Path) -> None:
    """Lintro falls back to killing npx alone, says so, and does not fail.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    messages: list[str] = []
    sink = logger.add(messages.append, level="DEBUG", format="{message}")
    child = _start_parent(tmp_path)
    pids = _read_pids(tmp_path / "ids")
    try:
        stop_process(
            child,
            tree=True,
            table=ProcessTable(read_all=lambda: None, read=lambda _: None),
        )

        assert_that(child.returncode).is_not_none()
        assert_that(_gone(pids["grandchild"], budget=0.5)).is_false()
        assert_that("".join(messages)).contains("could not read the process table")
    finally:
        logger.remove(sink)
        _kill_quietly(pids["grandchild"])


def test_a_child_that_exited_by_itself_leaves_its_children_alone(
    tmp_path: Path,
) -> None:
    """Once npx is reaped its id, and its children's, may be reused.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    child = _start_parent(tmp_path, then="sys.exit(0)\n")
    child.wait()
    grandchild = _read_pids(tmp_path / "ids")["grandchild"]
    try:
        stop_process(child, tree=True)

        assert_that(_gone(grandchild, budget=0.5)).is_false()
    finally:
        _kill_quietly(grandchild)


def test_a_ctrl_c_while_stopping_the_tree_still_stops_all_of_it(
    tmp_path: Path,
) -> None:
    """The interruption is re-raised once the tree is gone and npx reaped.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    child = _start_parent(tmp_path)
    pids = _read_pids(tmp_path / "ids")
    reads: list[bool] = []
    system = _system()

    def interrupted_once() -> Mapping[int, Identity] | None:
        if not reads:
            reads.append(True)
            raise KeyboardInterrupt
        return system.read_all()

    try:
        with pytest.raises(KeyboardInterrupt):
            stop_process(
                child,
                tree=True,
                table=ProcessTable(
                    read_all=interrupted_once,
                    read=system.read,
                    pidfd=system.pidfd,
                ),
            )

        assert_that(child.returncode).is_not_none()
        assert_that(_gone(pids["grandchild"])).is_true()
        assert_that(_stays_still(tmp_path / "log")).is_true()
    finally:
        _kill_quietly(*(pid for name, pid in pids.items() if "child" in name))


#: Runs lintro's side in its own session, so no signal reaches pytest, and
#: prints one JSON line. ``ctrl-c``: the child sends SIGINT to lintro alone
#: while it runs (npx keeps running). ``lie``: the process table claims that
#: lintro itself descends from the child, and a bystander shares lintro's
#: process group, which a group signal would reach.
_DRIVER = """
import json, os, subprocess, sys, time
mode, parent, log, ids = sys.argv[1:5]
from lintro.utils.process_stop import run_reaped, stop_process
from lintro.utils.process_tree import Identity, ProcessTable, system_table
if mode == "ctrl-c":
    try:
        run_reaped([sys.executable, "-c", parent, log, ids], timeout=60.0)
    except KeyboardInterrupt:
        print(json.dumps({"ended": "KeyboardInterrupt"}), flush=True)
    raise SystemExit(0)
bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
child = subprocess.Popen([sys.executable, "-c", parent, log, ids])
while not os.path.exists(ids):
    time.sleep(0.02)
system = system_table()
def lying():
    table = dict(system.read_all() or {})
    own = table[os.getpid()]
    table[os.getpid()] = Identity(child.pid, own.started)
    return table
stop_process(
    child,
    tree=True,
    table=ProcessTable(read_all=lying, read=system.read, pidfd=system.pidfd),
)
alive = bystander.poll() is None
bystander.kill()
bystander.wait()
print(json.dumps({"ended": "survived", "bystander": alive}), flush=True)
"""


def _drive(tmp_path: Path, mode: str, then: str = "") -> dict[str, object]:
    """Run :data:`_DRIVER` in a new session and return what it printed.

    Args:
        tmp_path: Where the driver's child writes its log and ids.
        mode: ``ctrl-c`` or ``lie``.
        then: What the driver's child runs once its ids are recorded.

    Returns:
        The driver's JSON line, parsed.
    """
    driver = subprocess.run(  # nosec B603 - fixed argv
        [
            sys.executable,
            "-c",
            _DRIVER,
            mode,
            _PARENT.format(then=then),
            str(tmp_path / "log"),
            str(tmp_path / "ids"),
        ],
        capture_output=True,
        text=True,
        timeout=60.0,
        start_new_session=True,
        check=False,
    )
    assert_that(driver.returncode).described_as(driver.stderr).is_equal_to(0)
    result: dict[str, object] = json.loads(driver.stdout.splitlines()[-1])
    return result


def test_ctrl_c_in_lintro_alone_stops_the_child_and_what_it_started(
    tmp_path: Path,
) -> None:
    """A ``KeyboardInterrupt`` while npx runs reaps it and ends its children.

    The signal goes to lintro's process only, so npx is still running when
    lintro handles it; ``subprocess.run`` would kill npx alone and wait a
    quarter of a second for it.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    pids: dict[str, int] = {}
    try:
        result = _drive(
            tmp_path,
            "ctrl-c",
            then="import signal\nos.kill(os.getppid(), signal.SIGINT)\n",
        )
        pids = _read_pids(tmp_path / "ids")

        assert_that(result).is_equal_to({"ended": "KeyboardInterrupt"})
        assert_that(_gone(pids["child"])).is_true()
        assert_that(_gone(pids["grandchild"])).is_true()
        assert_that(_stays_still(tmp_path / "log")).is_true()
    finally:
        _kill_quietly(*(pid for name, pid in pids.items() if "child" in name))


def test_only_verified_descendants_are_signalled(tmp_path: Path) -> None:
    """Lintro and the rest of its group survive; no group is signalled.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    pids: dict[str, int] = {}
    try:
        result = _drive(tmp_path, "lie")
        pids = _read_pids(tmp_path / "ids")

        assert_that(result).is_equal_to({"ended": "survived", "bystander": True})
        assert_that(_gone(pids["grandchild"])).is_true()
    finally:
        _kill_quietly(*(pid for name, pid in pids.items() if "child" in name))


class _InterruptedOnce(subprocess.Popen[str]):
    """A child whose first unbounded wait is cut short by a second Ctrl-C."""

    interrupts: int = 1

    def wait(self, timeout: float | None = None) -> int:
        """Raise ``KeyboardInterrupt`` once, then wait as usual.

        Args:
            timeout: As for :meth:`subprocess.Popen.wait`.

        Returns:
            The exit status.

        Raises:
            KeyboardInterrupt: On the first wait without a timeout.
        """
        if timeout is None and self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        return super().wait(timeout)


def test_a_second_ctrl_c_during_the_wait_still_reaps_the_child() -> None:
    """The interruption is re-raised only once the child has exited."""
    child = _InterruptedOnce(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )

    with pytest.raises(KeyboardInterrupt):
        stop_process(child)

    assert_that(child.returncode).is_not_none()
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)


class _Unkillable(subprocess.Popen[str]):
    """A child that a kill does not end, as one stuck in the kernel."""

    def kill(self) -> None:
        """Do nothing."""


def test_a_bounded_stop_gives_up_on_a_child_that_cannot_exit() -> None:
    """Outside an npx install, a stuck child cannot hang lintro."""
    child = _Unkillable(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )
    returned: list[bool] = []

    def stop() -> None:
        stop_process(child, wait_seconds=0.2)
        returned.append(True)

    stopper = threading.Thread(target=stop, daemon=True)
    try:
        stopper.start()
        stopper.join(timeout=10.0)

        assert_that(returned).is_equal_to([True])
        assert_that(child.poll()).is_none()
    finally:
        _kill_quietly(child.pid)
        child.wait()


class _InterruptedWaits(_Unkillable):
    """An unkillable child whose first *interrupts* waits are cut short."""

    def __init__(self, *args: Any, interrupts: int, **kwargs: Any) -> None:
        """Start the child.

        Args:
            *args: As for :class:`subprocess.Popen`.
            interrupts: How many waits raise ``KeyboardInterrupt``.
            **kwargs: As for :class:`subprocess.Popen`.
        """
        super().__init__(*args, **kwargs)
        self.interrupts = interrupts

    def wait(self, timeout: float | None = None) -> int:
        """Raise ``KeyboardInterrupt`` while any are left, then wait.

        Args:
            timeout: As for :meth:`subprocess.Popen.wait`.

        Returns:
            The exit status.

        Raises:
            KeyboardInterrupt: While interruptions are left.
        """
        if self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        return super().wait(timeout)


def _stop_in_thread(
    child: subprocess.Popen[str],
    budget: float = 10.0,
) -> tuple[list[KeyboardInterrupt], float]:
    """Stop *child* after a Ctrl-C, without letting a hang hold the test.

    Args:
        child: The child.
        budget: Longest wait for the stop, in seconds.

    Returns:
        What the stop raised, and how long it took.
    """
    raised: list[KeyboardInterrupt] = []

    def stop() -> None:
        try:
            stop_process(child, pending=KeyboardInterrupt())
        except KeyboardInterrupt as exc:
            raised.append(exc)

    began = time.monotonic()
    stopper = threading.Thread(target=stop, daemon=True)
    stopper.start()
    stopper.join(timeout=budget)
    return raised, time.monotonic() - began


def test_a_second_ctrl_c_still_waits_for_a_child_that_exits() -> None:
    """A casual second Ctrl-C does not end the wait; the child is reaped."""
    child = _InterruptedWaits(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(1)"],
        text=True,
        interrupts=1,
    )
    try:
        raised, _ = _stop_in_thread(child)

        assert_that(raised).is_empty()
        assert_that(child.returncode).is_equal_to(0)
    finally:
        _kill_quietly(child.pid)
        child.wait()


def test_ctrl_c_stops_waiting_for_a_child_sigkill_did_not_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the deadline lintro raises, leaving the child unreaped.

    Args:
        monkeypatch: Shortens the deadline.
    """
    monkeypatch.setattr(process_stop, "_STUCK_SECONDS", 0.3)
    child = _Unkillable(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )
    try:
        raised, took = _stop_in_thread(child)

        assert_that(raised).is_length(1)
        assert_that(raised[0]).is_instance_of(ChildStillRunningError)
        assert_that(took).is_less_than(5.0)
        assert_that(child.returncode).is_none()
    finally:
        _kill_quietly(child.pid)
        child.wait()


def test_a_third_ctrl_c_stops_waiting_at_once() -> None:
    """The deadline is not waited for once the user has insisted."""
    child = _InterruptedWaits(  # nosec B603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
        interrupts=1_000,
    )
    try:
        raised, took = _stop_in_thread(child)

        assert_that(raised).is_length(1)
        assert_that(raised[0]).is_instance_of(ChildStillRunningError)
        assert_that(raised[0].__cause__).is_instance_of(KeyboardInterrupt)
        assert_that(took).is_less_than(process_stop._STUCK_SECONDS)
        assert_that(child.interrupts).is_equal_to(1_000 - 2)
    finally:
        child.interrupts = 0
        _kill_quietly(child.pid)
        child.wait()
