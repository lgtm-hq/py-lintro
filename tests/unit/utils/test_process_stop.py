"""Stopping a child process before lintro cleans up after it (#3530)."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - spawns a sleeping child, argv list only
import sys
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
