"""Stopping a child process before lintro cleans up after it (#3530)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - spawns a sleeping child, argv list only
import sys
import threading
import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.process_stop import run_reaped, stop_process

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the child is a POSIX sleep; Windows is exercised through windows=True",
)


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


def test_a_timeout_stops_what_the_child_started_too(tmp_path: Path) -> None:
    """The install scripts npm runs under npx go when npx is stopped.

    The child leads its own session and group, so a Ctrl-C at the terminal
    reaches lintro alone, and lintro ends the whole group.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    log, ids = tmp_path / "log", tmp_path / "ids"
    script = _PARENT.format(then="")
    pids: dict[str, int] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_reaped(
                [sys.executable, "-c", script, str(log), str(ids)],
                timeout=3.0,
            )
        pids = _read_pids(ids)
        assert_that(pids["group"]).is_equal_to(pids["child"])
        assert_that(pids["session"]).is_equal_to(pids["child"])
        assert_that(pids["session"]).is_not_equal_to(os.getsid(0))
        assert_that(_gone(pids["grandchild"])).is_true()
        assert_that(_stays_still(log)).is_true()
    finally:
        _kill_quietly(*(pid for name, pid in pids.items() if "child" in name))


def test_ctrl_c_in_lintro_stops_the_child_and_what_it_started(
    tmp_path: Path,
) -> None:
    """A ``KeyboardInterrupt`` mid-run reaps the child and ends its group.

    The child sends lintro ``SIGINT`` itself once it is running, as a
    Ctrl-C at the terminal would; ``subprocess.run`` would leave it
    unreaped after a quarter of a second, and its children running.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    log, ids = tmp_path / "log", tmp_path / "ids"
    script = _PARENT.format(
        then="import signal\nos.kill(os.getppid(), signal.SIGINT)\n",
    )
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    pids: dict[str, int] = {}
    try:
        with pytest.raises(KeyboardInterrupt):
            run_reaped(
                [sys.executable, "-c", script, str(log), str(ids)],
                timeout=30.0,
            )
        pids = _read_pids(ids)
        with pytest.raises(ProcessLookupError):
            os.kill(pids["child"], 0)
        assert_that(_gone(pids["grandchild"])).is_true()
        assert_that(_stays_still(log)).is_true()
    finally:
        signal.signal(signal.SIGINT, previous)
        _kill_quietly(*(pid for name, pid in pids.items() if "child" in name))


def test_a_child_that_exited_by_itself_leaves_its_former_group_alone(
    tmp_path: Path,
) -> None:
    """Once the leader is reaped its group id may be reused: nothing is signalled.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    log, ids = tmp_path / "log", tmp_path / "ids"
    script = _PARENT.format(then="sys.exit(0)\n")
    leader = subprocess.Popen(  # nosec B603 - fixed argv
        [sys.executable, "-c", script, str(log), str(ids)],
        start_new_session=True,
    )
    leader.wait()
    grandchild = _read_pids(ids)["grandchild"]
    try:
        stop_process(leader, group=True)

        assert_that(_gone(grandchild, budget=0.5)).is_false()
    finally:
        _kill_quietly(grandchild)


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
