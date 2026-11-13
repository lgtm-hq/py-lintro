"""lintro's npx cache repair against npm's real ``concurrency.lock`` (#3422).

Each test drives ``libnpmexec``'s own ``with-lock.js`` from the installed npm,
so npm judges staleness, reclaims locks and detects compromise exactly as
npx does. A lock npm holds must never be deleted, moved for good, or have its
entry removed while npm uses it; losing a race ends in waiting, refusal or a
leaked lock, never in lintro carrying on.
"""

from __future__ import annotations

import contextlib
import os
import queue
import shutil
import signal
import subprocess  # nosec B404 - runs node with a fixed argv; shell=False
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core import npx_cache
from lintro.tools.core.npx_cache import repair_npx_cache
from lintro.utils import file_cache

_NODE = shutil.which("node")
_NPM = shutil.which("npm")


def _with_lock_js() -> Path | None:
    """Locate npm's own lock implementation.

    Returns:
        ``with-lock.js`` from the npm on PATH, or None when there is none.
    """
    if _NODE is None or _NPM is None:
        return None
    try:
        root = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [_NPM, "root", "-g"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    script = Path(root) / "npm/node_modules/libnpmexec/lib/with-lock.js"
    return script if script.is_file() else None


_WITH_LOCK = _with_lock_js()

pytestmark = pytest.mark.skipif(
    _WITH_LOCK is None,
    reason="node and npm, with libnpmexec's with-lock.js, are required",
)

#: Holds npm's lock for argv[3] milliseconds, reporting what happened.
_NPM_HOLDER = """
const withLock = require(process.argv[1]);
const fs = require('fs');
withLock(process.argv[2], async () => {
  console.log('HELD:' + fs.statSync(process.argv[2]).ino);
  await new Promise(resolve => setTimeout(resolve, Number(process.argv[3])));
}).then(
  () => console.log('NORMAL'),
  error => { console.log('FAILED:' + error.code); process.exitCode = 1; },
);
"""

_PIN = "3.9.4"

#: A path argument as ``os.rename`` and ``Path.rmdir`` receive one.
_PathArg = os.PathLike[str] | str

#: Longest wait for npm to report that it holds a lock.
_HELD_DEADLINE_SECONDS = 30.0

#: Every npm process a test starts, killed at teardown if still running.
_STARTED: list[subprocess.Popen[str]] = []


@pytest.fixture(autouse=True)
def _reap_npm() -> Iterator[None]:
    """Kill any npm process a test left running, whatever the outcome.

    Yields:
        None: This fixture is used for its side effect only.
    """
    yield
    while _STARTED:
        npm = _STARTED.pop()
        if npm.poll() is None:
            npm.kill()
        with contextlib.suppress(subprocess.SubprocessError, OSError, ValueError):
            npm.communicate(timeout=_HELD_DEADLINE_SECONDS)


def _npm_holds(lock: Path, millis: int) -> subprocess.Popen[str]:
    """Start real npm taking *lock* and holding it.

    Args:
        lock: ``<entry>/concurrency.lock``.
        millis: How long npm keeps the lock once it has it.

    Returns:
        The running node process.
    """
    assert _NODE is not None and _WITH_LOCK is not None
    npm = subprocess.Popen(  # nosec B603 - fixed argv, shell=False
        [_NODE, "-e", _NPM_HOLDER, str(_WITH_LOCK), str(lock), str(millis)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _STARTED.append(npm)
    return npm


def _age(path: Path, seconds: float = 120) -> None:
    """Backdate *path* so npm and lintro judge it stale.

    Args:
        path: Lock directory.
        seconds: How far back to set its times.
    """
    old = time.time() - seconds
    os.utime(path, (old, old))


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Lintro's per-pin prettier cache, under a private cache root.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped override of lintro's cache root.

    Returns:
        The per-pin cache directory.
    """
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / "lintro-cache")
    return npx_cache.npx_cache_dir(f"prettier@{_PIN}")


def _start_and_wait_held(lock: Path, millis: int) -> subprocess.Popen[str]:
    """Start real npm on *lock* and return once it holds the lock.

    The ``HELD`` line is read on a helper thread with a deadline, so a node
    process that exits early or never gets the lock fails the test instead of
    hanging the run.

    Args:
        lock: ``<entry>/concurrency.lock``.
        millis: How long npm keeps the lock.

    Returns:
        The running node process, past its ``HELD`` line.
    """
    npm = _npm_holds(lock, millis)
    stdout = npm.stdout
    assert stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: lines.put(stdout.readline()), daemon=True).start()
    line = ""
    try:
        with contextlib.suppress(queue.Empty):
            line = lines.get(timeout=_HELD_DEADLINE_SECONDS)
        if not line.startswith("HELD:"):
            pytest.fail(f"npm never reported holding {lock}: {line!r}")
    finally:
        if not line.startswith("HELD:"):
            npm.kill()
            with contextlib.suppress(subprocess.SubprocessError, OSError, ValueError):
                npm.communicate(timeout=_HELD_DEADLINE_SECONDS)
    return npm


def test_npm_reclaiming_a_stale_lock_while_lintro_looks_is_left_alone(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sol's stale-lock reproduction from PR #3420: npm wins, lintro refuses.

    lintro has just judged the lock abandoned when npm reclaims it and holds
    a fresh lock. lintro must not touch npm's lock or entry: it refuses and
    names the path, and npm's callback finishes normally.

    Args:
        cache: Per-pin cache.
        monkeypatch: Places npm's reclaim right after lintro's staleness read.
    """
    entry = cache / "_npx" / "0123456789abcdef"
    lock = entry / "concurrency.lock"
    lock.mkdir(parents=True)
    _age(lock)
    npm: list[subprocess.Popen[str]] = []
    read_age = npx_cache._lock_age

    def npm_reclaims_after_the_read(path: Path) -> float:
        age = read_age(path)
        if not npm:
            npm.append(_start_and_wait_held(lock, 1500))
        return age

    monkeypatch.setattr(npx_cache, "_lock_age", npm_reclaims_after_the_read)
    try:
        with pytest.raises(npx_cache.NpxCacheError, match="looks abandoned") as refusal:
            repair_npx_cache(
                cache,
                package="prettier",
                pin=_PIN,
                binary="prettier",
                pathsep=":",
            )
    finally:
        output, _ = npm[0].communicate(timeout=30) if npm else ("", None)

    assert_that(str(refusal.value)).contains(f"Delete {lock}")
    assert_that(output).contains("NORMAL").does_not_contain("FAILED")
    assert_that(entry.is_dir()).is_true()


def test_npm_taking_over_a_lock_lintro_held_too_long_keeps_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sol's delayed-release reproduction from PR #3420: npm keeps its lock.

    lintro is delayed past npm's threshold in release, after its ownership
    check (here its lock is backdated rather than waiting 61 seconds), and
    npm reclaims the lock. lintro's release moves npm's lock aside, sees it
    is not its own, and puts it back, so npm's callback completes normally.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Places npm's takeover inside lintro's release.
    """
    (tmp_path / "probe").mkdir()
    if npx_cache._rename_noreplace(tmp_path / "probe", tmp_path / "moved") is None:
        pytest.skip("no exclusive rename here; the lock is left aside instead")
    entry = tmp_path / "entry"
    entry.mkdir()
    lock = entry / "concurrency.lock"
    held = npx_cache._acquire(lock)
    npm: list[subprocess.Popen[str]] = []
    real_rename = os.rename

    def npm_takes_over_before_the_move(src: _PathArg, dst: _PathArg) -> None:
        if not npm and Path(src) == lock:
            _age(lock)
            npm.append(_start_and_wait_held(lock, 1500))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", npm_takes_over_before_the_move)
    npx_cache._release(lock, held)
    monkeypatch.undo()
    output, _ = npm[0].communicate(timeout=30)

    assert_that(output).contains("NORMAL").does_not_contain("FAILED")
    assert_that(list(entry.iterdir())).is_empty()


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGKILL")
def test_npx_alone_recovers_a_lock_left_by_a_killed_lintro(tmp_path: Path) -> None:
    """A lintro run killed while holding a lock never blocks plain npx.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    entry = tmp_path / "entry"
    entry.mkdir()
    lock = entry / "concurrency.lock"
    killed = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [
            sys.executable,
            "-c",
            "import os, signal, sys\n"
            "from pathlib import Path\n"
            "from lintro.tools.core import npx_cache\n"
            "npx_cache._acquire(Path(sys.argv[1]))\n"
            "os.kill(os.getpid(), signal.SIGKILL)\n",
            str(lock),
        ],
        check=False,
        timeout=60,
    )
    _age(lock)
    output, _ = _npm_holds(lock, 100).communicate(timeout=30)

    assert_that(killed.returncode).is_equal_to(-signal.SIGKILL)
    assert_that(output).contains("HELD", "NORMAL")
    assert_that(lock.exists()).is_false()


def test_npm_waits_for_a_lock_lintro_holds_then_takes_it(tmp_path: Path) -> None:
    """While lintro holds an entry's lock, real npm waits rather than failing.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    entry = tmp_path / "entry"
    entry.mkdir()
    lock = entry / "concurrency.lock"
    held = npx_cache._acquire(lock)
    npm = _npm_holds(lock, 100)
    time.sleep(1.5)
    npm_waited = npm.poll() is None and npx_cache._holds(lock, held)
    npx_cache._release(lock, held)
    output, _ = npm.communicate(timeout=30)

    assert_that(npm_waited).is_true()
    assert_that(output).contains("HELD", "NORMAL")
    assert_that(lock.exists()).is_false()
