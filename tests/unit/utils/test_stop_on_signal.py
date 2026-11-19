"""A stop signal to lintro stops every live npx, in any thread (#3530).

npx runs in its own process group so a timeout can kill its install scripts
with it, which keeps the terminal's Ctrl-C from reaching it. These tests run
lintro in a child process (so no signal reaches the pytest worker), start a
fake npx that leaves a half-written entry and a grandchild that keeps
writing, as a ``postinstall`` does, then signal lintro's process group the
way a terminal does.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - runs the lintro driver, argv list only
import sys
import threading
import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.npx_integrity import cache_entries
from lintro.utils.stop_on_signal import signal_guard

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="process groups and POSIX signals",
)

_SPEC = "prettier@3.9.4"
_ENTRY = "0123456789abcdef"

#: Runs npx through lintro, in a worker thread of the real parallel executor,
#: in a caller's own thread pool, or on the main thread, and reports how the run ended. ``inject`` sends
#: lintro a second SIGINT just before it kills npx's group (``killpg``) or
#: just before it cleans up (``cleanup``).
_DRIVER = """
import asyncio, os, signal, subprocess, sys, types
mode, inject, npx, cache, timeout = sys.argv[1:6]
command = [npx, "--yes", f"--cache={cache}", "--package", "prettier@3.9.4", "prettier"]

from lintro.plugins.subprocess_executor import run_subprocess_streaming

if inject == "killpg":
    real_killpg = os.killpg
    def killpg(pid, sig):
        signal.raise_signal(signal.SIGINT)
        real_killpg(pid, sig)
    os.killpg = killpg
elif inject == "cleanup":
    from lintro.utils.npx_unfinished import BoundCache
    real_discard = BoundCache.discard_unfinished
    def discard(self, **kwargs):
        signal.raise_signal(signal.SIGINT)
        return real_discard(self, **kwargs)
    BoundCache.discard_unfinished = discard

def check(paths, options):
    run_subprocess_streaming(command, timeout=float(timeout))

try:
    if mode == "worker":
        from lintro.enums.action import Action
        from lintro.utils.async_tool_executor import AsyncToolExecutor
        tool = types.SimpleNamespace(
            definition=types.SimpleNamespace(name="fake"), check=check,
        )
        executor = AsyncToolExecutor(max_workers=2)
        try:
            asyncio.run(executor.run_tools_parallel([("fake", tool)], ["."], Action.CHECK))
        finally:
            executor.shutdown()
    elif mode == "pool":
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=1)
        pool.submit(check, [], {}).result()
        raise SystemExit(0)
    else:
        check([], {})
except KeyboardInterrupt:
    if mode == "pool":
        raise  # Uncaught, as in a caller that does not handle Ctrl-C.
    print("ended: KeyboardInterrupt", flush=True)
    sys.exit(130)
except subprocess.TimeoutExpired:
    print("ended: TimeoutExpired", flush=True)
    sys.exit(3)
print("ended: finished", flush=True)
"""


def _fake_npx(directory: Path, cache: Path, ids: Path, log: Path) -> Path:
    """Write a fake npx that half-installs, starts a writer, then hangs.

    npm links the executables and runs install scripts before it writes
    ``package-lock.json``; the writer stands in for a ``postinstall`` that
    keeps going.

    Args:
        directory: Where the script goes.
        cache: Per-pin npm cache.
        ids: Where npx records its own and the writer's process id.
        log: What the writer keeps appending to.

    Returns:
        The script's path.
    """
    entry = cache / "_npx" / _ENTRY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "npx"
    path.write_text(
        "#!/bin/sh\n"
        f"mkdir -p '{entry}/node_modules/prettier' '{entry}/concurrency.lock'\n"
        f"mkdir -p '{entry}/node_modules/.bin'\n"
        f"echo '{{}}' > '{entry}/node_modules/prettier/package.json'\n"
        f"ln -s ../prettier/package.json '{entry}/node_modules/.bin/prettier'\n"
        f"( while :; do echo x >> '{log}'; sleep 0.02; done ) &\n"
        f"echo \"npx=$$ writer=$!\" > '{ids}.tmp'\n"
        f"mv '{ids}.tmp' '{ids}'\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _alive(pid: int) -> bool:
    """Report whether a process, zombie or not, still has *pid*.

    Args:
        pid: The process id.

    Returns:
        False once it has exited and been reaped.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone(pid: int, budget: float = 10.0) -> bool:
    """Report whether *pid* is gone within *budget* seconds.

    Args:
        pid: The process id.
        budget: Longest wait in seconds.

    Returns:
        True once no process has *pid*.
    """
    give_up = time.monotonic() + budget
    while time.monotonic() < give_up:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return False


class _Lintro:
    """The driver running lintro in its own process group, as a terminal job."""

    def __init__(self, tmp_path: Path, mode: str, inject: str, timeout: float) -> None:
        """Start the driver.

        Args:
            tmp_path: Temporary directory provided by pytest.
            mode: ``worker``, ``pool`` or ``main``: where npx is started from.
            inject: ``none``, ``killpg`` or ``cleanup``.
            timeout: The tool timeout lintro is given.
        """
        self.cache = Path(os.path.realpath(tmp_path)) / "npx" / _SPEC
        self.ids, self.log = tmp_path / "ids", tmp_path / "postinstall.log"
        npx = _fake_npx(tmp_path / "bin", self.cache, self.ids, self.log)
        driver = tmp_path / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        env = {**os.environ, "HOME": str(tmp_path / "home")}
        self.process = subprocess.Popen(  # nosec B603 - fixed argv
            [sys.executable, str(driver), mode, inject, str(npx), str(self.cache)]
            + [str(timeout)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
        )
        self.pids: dict[str, int] = {}

    def wait_for_npx(self, budget: float = 30.0) -> dict[str, int]:
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
        words = self.ids.read_text(encoding="utf-8").split()
        self.pids = {k: int(v) for k, v in (word.split("=") for word in words)}
        return self.pids

    def finish(self, budget: float) -> tuple[int, str, float]:
        """Wait for lintro to exit.

        Args:
            budget: Longest wait in seconds; then lintro is killed.

        Returns:
            Its exit status, its output and how long it took.
        """
        started = time.monotonic()
        try:
            output, _ = self.process.communicate(timeout=budget)
        except subprocess.TimeoutExpired:
            self.kill()
            output, _ = self.process.communicate()
        return self.process.returncode, output, time.monotonic() - started

    def kill(self) -> None:
        """Kill whatever is left of lintro, npx and the writer."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)
        for pid in self.pids.values():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def _stays_still(log: Path) -> bool:
    """Report whether nothing appends to *log* any more.

    Args:
        log: The writer's log.

    Returns:
        True when its size does not change over half a second.
    """
    before = log.stat().st_size if log.exists() else 0
    time.sleep(0.5)
    return (log.stat().st_size if log.exists() else 0) == before


_SIGNALS = {
    "SIGINT": (signal.SIGINT, 130),
    "SIGTERM": (signal.SIGTERM, -signal.SIGTERM),
    "SIGHUP": (signal.SIGHUP, -signal.SIGHUP),
}


@pytest.mark.parametrize("mode", ["worker", "main"])
@pytest.mark.parametrize("name", list(_SIGNALS))
def test_a_stop_signal_to_lintros_group_stops_npx_and_its_children(
    tmp_path: Path,
    mode: str,
    name: str,
) -> None:
    """Lintro exits promptly, npx's writer is gone and the entry removed.

    The signal goes to lintro's process group, as a terminal sends Ctrl-C
    or a hang-up; npx, in its own group, does not receive it.

    Args:
        tmp_path: Temporary directory provided by pytest.
        mode: Whether npx runs in a parallel worker thread or the main one.
        name: The signal.
    """
    signum, status = _SIGNALS[name]
    lintro = _Lintro(tmp_path, mode, "none", timeout=60.0)
    try:
        pids = lintro.wait_for_npx()
        os.killpg(lintro.process.pid, signum)
        returncode, output, took = lintro.finish(budget=15.0)

        assert_that(took).described_as(output).is_less_than(10.0)
        assert_that(returncode).described_as(output).is_equal_to(status)
        assert_that(_gone(pids["npx"])).is_true()
        assert_that(_gone(pids["writer"])).is_true()
        assert_that(_stays_still(lintro.log)).is_true()
        assert_that(cache_entries(lintro.cache)).is_empty()
    finally:
        lintro.kill()


@pytest.mark.parametrize("inject", ["killpg", "cleanup"])
def test_a_second_ctrl_c_waits_until_npx_is_stopped_and_cleaned_up(
    tmp_path: Path,
    inject: str,
) -> None:
    """A Ctrl-C while lintro stops npx after a timeout is raised afterwards.

    It arrives just before the group is killed, or just before the
    unfinished entry is removed. Raised there, it would skip the kill
    (leaving npx and its writer running after the lock is released) or the
    cleanup (leaving the entry).

    Args:
        tmp_path: Temporary directory provided by pytest.
        inject: Where the interruption arrives.
    """
    lintro = _Lintro(tmp_path, "main", inject, timeout=2.0)
    try:
        pids = lintro.wait_for_npx()
        returncode, output, _ = lintro.finish(budget=20.0)

        assert_that(output).contains("ended: KeyboardInterrupt")
        assert_that(returncode).is_equal_to(130)
        assert_that(_gone(pids["writer"], budget=2.0)).is_true()
        assert_that(_alive(pids["npx"])).is_false()
        assert_that(cache_entries(lintro.cache)).is_empty()
    finally:
        lintro.kill()


def test_ctrl_c_stops_npx_in_a_callers_own_thread_pool(tmp_path: Path) -> None:
    """No lintro code runs on the main thread, and Ctrl-C still stops npx.

    The ``KeyboardInterrupt`` ends the main thread; before the interpreter
    waits for the pool's thread, lintro asks its npx run to stop.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    lintro = _Lintro(tmp_path, "pool", "none", timeout=60.0)
    try:
        pids = lintro.wait_for_npx()
        os.killpg(lintro.process.pid, signal.SIGINT)
        returncode, output, took = lintro.finish(budget=15.0)

        assert_that(took).described_as(output).is_less_than(10.0)
        assert_that(returncode).described_as(output).is_equal_to(-signal.SIGINT)
        assert_that(_gone(pids["writer"])).is_true()
        assert_that(cache_entries(lintro.cache)).is_empty()
    finally:
        lintro.kill()


def test_the_guard_chains_and_restores_the_previous_handler() -> None:
    """A library's own handler still runs, and is back once the run ends."""
    seen: list[int] = []

    def library_handler(signum: int, frame: object) -> None:
        seen.append(signum)

    previous = signal.signal(signal.SIGHUP, library_handler)
    try:
        with signal_guard():
            assert_that(signal.getsignal(signal.SIGHUP)).is_not_equal_to(
                library_handler,
            )
            signal.raise_signal(signal.SIGHUP)
        assert_that(seen).is_equal_to([signal.SIGHUP])
        assert_that(signal.getsignal(signal.SIGHUP)).is_equal_to(library_handler)
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_no_handler_is_installed_from_a_worker_thread() -> None:
    """Off the main thread the guard leaves every handler as it was."""
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    during: list[dict[signal.Signals, object]] = []

    def worker() -> None:
        with signal_guard():
            during.append({s: signal.getsignal(s) for s in before})

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10.0)

    assert_that(during).is_equal_to([before])
