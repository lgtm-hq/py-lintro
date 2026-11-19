"""Signals reach npx the way they reach any tool lintro runs (#3530).

npx runs in lintro's process group, so a Ctrl-C at the terminal reaches it
and its install scripts directly, and lintro installs no signal handler of
its own: ``SIGTERM`` ends lintro at once, even while it waits for another
run's install lock. lintro runs in a child process in its own session here,
so no signal reaches the pytest worker; the signals go to that child's
process group, as a terminal or ``docker stop`` sends them.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - runs the lintro driver, argv list only
import sys
import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.npx_install_lock import install_lock

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX process groups and signals",
)

_SPEC = "prettier@3.9.4"

#: Runs npx through lintro on the main thread or in a worker thread of the
#: real parallel executor. Prints ``ready`` just before lintro starts.
_DRIVER = """
import asyncio, sys, types
mode, npx, cache = sys.argv[1:4]
command = [npx, "--yes", f"--cache={cache}", "--package", "prettier@3.9.4", "prettier"]
from lintro.plugins.subprocess_executor import run_subprocess

def check(paths, options):
    run_subprocess(command, timeout=60.0)

print("ready", flush=True)
if mode == "worker":
    from lintro.enums.action import Action
    from lintro.utils.async_tool_executor import AsyncToolExecutor
    tool = types.SimpleNamespace(definition=types.SimpleNamespace(name="fake"), check=check)
    executor = AsyncToolExecutor(max_workers=2)
    try:
        asyncio.run(executor.run_tools_parallel([("fake", tool)], ["."], Action.CHECK))
    finally:
        executor.shutdown()
else:
    check([], {})
"""

#: The fake npx: it starts a writer as npm starts a ``postinstall`` (an
#: ordinary child, not a shell background job, so it keeps the default
#: action for SIGINT), records both ids, then waits.
_NPX = """
import os, subprocess, sys, time
log, ids = sys.argv[1:3]
writer = subprocess.Popen([sys.executable, "-c",
    "import sys, time\\nwhile True:\\n    open(sys.argv[1], 'a').write('x')\\n"
    "    time.sleep(0.02)\\n", log])
with open(ids + ".tmp", "w") as out:
    out.write(f"{os.getpid()} {writer.pid}")
os.rename(ids + ".tmp", ids)
time.sleep(60)
"""


class _Lintro:
    """lintro running npx in its own session and group, as a terminal job."""

    def __init__(self, tmp_path: Path, mode: str) -> None:
        """Start the driver and wait until lintro is about to run.

        Args:
            tmp_path: Temporary directory provided by pytest.
            mode: ``main`` or ``worker``: which thread runs npx.
        """
        self.cache = Path(os.path.realpath(tmp_path)) / "npx" / _SPEC
        self.ids, self.log = tmp_path / "ids", tmp_path / "postinstall.log"
        script = tmp_path / "npx.py"
        script.write_text(_NPX, encoding="utf-8")
        npx = tmp_path / "bin" / "npx"
        npx.parent.mkdir()
        npx.write_text(
            f"#!/bin/sh\nexec '{sys.executable}' '{script}' '{self.log}' '{self.ids}'\n",
            encoding="utf-8",
        )
        npx.chmod(0o755)
        driver = tmp_path / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        self.process = subprocess.Popen(  # nosec B603 - fixed argv
            [sys.executable, str(driver), mode, str(npx), str(self.cache)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "HOME": str(tmp_path / "home")},
            start_new_session=True,
        )
        assert self.process.stdout is not None
        assert_that(self.process.stdout.readline().strip()).is_equal_to("ready")

    def npx_ids(self, budget: float = 30.0) -> tuple[int, int]:
        """Wait until npx runs and has started its writer.

        Args:
            budget: Longest wait in seconds.

        Returns:
            npx's and the writer's process ids.
        """
        give_up = time.monotonic() + budget
        while not self.ids.exists():
            assert_that(self.process.poll()).is_none()
            assert_that(time.monotonic()).is_less_than(give_up)
            time.sleep(0.02)
        npx, writer = self.ids.read_text(encoding="utf-8").split()
        return int(npx), int(writer)

    def signal_group(self, signum: int) -> float:
        """Send *signum* to lintro's group and time how long lintro takes to end.

        Args:
            signum: The signal.

        Returns:
            Seconds until lintro exited.
        """
        sent = time.monotonic()
        os.killpg(self.process.pid, signum)
        self.process.communicate(timeout=60.0)
        return time.monotonic() - sent

    def close(self) -> None:
        """Kill whatever is left of lintro's group."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.communicate(timeout=10.0)


def _gone(pid: int, budget: float = 10.0) -> bool:
    """Report whether *pid* is gone, or a zombie, within *budget* seconds.

    Args:
        pid: The process id.
        budget: Longest wait in seconds.

    Returns:
        True once no process runs as *pid*.
    """
    give_up = time.monotonic() + budget
    while time.monotonic() < give_up:
        state = subprocess.run(  # nosec B603 B607 - fixed argv
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("mode", ["main", "worker"])
def test_ctrl_c_at_the_terminal_reaches_npx_and_its_install_scripts(
    tmp_path: Path,
    mode: str,
) -> None:
    """Npx shares lintro's group, so the terminal's SIGINT stops it at once.

    With npx in a session of its own, npx and its postinstall would keep
    running; from a worker thread, lintro would wait for them.

    Args:
        tmp_path: Temporary directory provided by pytest.
        mode: Which thread runs npx.
    """
    lintro = _Lintro(tmp_path, mode)
    try:
        npx, writer = lintro.npx_ids()

        took = lintro.signal_group(signal.SIGINT)

        assert_that(took).is_less_than(10.0)
        assert_that(_gone(npx)).is_true()
        assert_that(_gone(writer)).is_true()
    finally:
        lintro.close()


def test_sigterm_while_waiting_for_the_install_lock_ends_lintro_at_once(
    tmp_path: Path,
) -> None:
    """No lintro handler holds SIGTERM back while another run installs.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    cache = Path(os.path.realpath(tmp_path)) / "npx" / _SPEC
    cache.mkdir(parents=True)
    with install_lock(cache, shared=False, deadline=time.monotonic() + 10.0):
        lintro = _Lintro(tmp_path, "worker")
        try:
            time.sleep(1.0)
            assert_that(lintro.process.poll()).is_none()

            took = lintro.signal_group(signal.SIGTERM)

            assert_that(took).is_less_than(5.0)
            assert_that(lintro.process.returncode).is_equal_to(-signal.SIGTERM)
            assert_that(lintro.ids.exists()).is_false()
        finally:
            lintro.close()
