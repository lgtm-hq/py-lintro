"""lintro's npx cache repair against npm's real ``concurrency.lock`` (#3422).

Each test drives ``libnpmexec``'s own ``with-lock.js`` from the installed npm,
so npm judges staleness, reclaims locks and detects compromise exactly as
npx does. A lock npm holds must never be deleted, moved for good, or have its
entry removed while npm uses it; losing a race ends in waiting, refusal or a
leaked lock, never in lintro carrying on.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - runs node with a fixed argv; shell=False
import time
from collections.abc import Callable
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


def _npm_holds(lock: Path, millis: int) -> subprocess.Popen[str]:
    """Start real npm taking *lock* and holding it.

    Args:
        lock: ``<entry>/concurrency.lock``.
        millis: How long npm keeps the lock once it has it.

    Returns:
        The running node process.
    """
    assert _NODE is not None and _WITH_LOCK is not None
    return subprocess.Popen(  # nosec B603 - fixed argv, shell=False
        [_NODE, "-e", _NPM_HOLDER, str(_WITH_LOCK), str(lock), str(millis)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


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


def _race_on_first_touch(
    monkeypatch: pytest.MonkeyPatch,
    lock: Path,
    race: Callable[[], None],
) -> list[bool]:
    """Run *race* the first time lintro removes or moves *lock* by name.

    Hooks both ``Path.rmdir`` and ``os.rename`` so the race lands at the
    same point, between lintro looking at the lock and acting on it,
    whichever call lintro uses.

    Args:
        monkeypatch: Installs the hooks.
        lock: The lock path to watch.
        race: What the other process does in that window.

    Returns:
        A list that holds True once the race has run.
    """
    fired: list[bool] = []
    real_rmdir, real_rename = Path.rmdir, os.rename

    def maybe_race(path: _PathArg) -> None:
        if not fired and Path(path) == lock:
            fired.append(True)
            race()

    def racing_rmdir(self: Path) -> None:
        maybe_race(self)
        real_rmdir(self)

    def racing_rename(src: _PathArg, dst: _PathArg) -> None:
        maybe_race(src)
        real_rename(src, dst)

    monkeypatch.setattr(Path, "rmdir", racing_rmdir)
    monkeypatch.setattr(os, "rename", racing_rename)
    return fired


def test_npm_reclaiming_a_stale_lock_first_keeps_its_lock_and_entry(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Npm wins the stale-lock race; lintro waits for it instead of deleting.

    Sol's stale-lock reproduction from PR #3420: lintro has judged the lock
    abandoned, then npm reclaims it and holds a fresh lock with a new inode
    before lintro acts. lintro must leave that lock and the entry alone
    while npm holds them, and repair only after npm releases.

    Args:
        cache: Per-pin cache.
        monkeypatch: Places npm's reclaim inside lintro's window.
    """
    entry = cache / "_npx" / "0123456789abcdef"
    lock = entry / "concurrency.lock"
    lock.mkdir(parents=True)
    _age(lock)
    npm: list[subprocess.Popen[str]] = []
    held: list[str] = []

    def npm_reclaims() -> None:
        npm.append(_npm_holds(lock, 2000))
        assert npm[0].stdout is not None
        held.append(npm[0].stdout.readline().strip())

    fired = _race_on_first_touch(monkeypatch, lock, npm_reclaims)
    try:
        repair_npx_cache(
            cache,
            package="prettier",
            pin=_PIN,
            binary="prettier",
            pathsep=":",
        )
    finally:
        output, _ = npm[0].communicate(timeout=30) if npm else ("", None)

    assert_that(fired).is_length(1)
    assert_that(held[0]).starts_with("HELD:")
    assert_that(output).contains("NORMAL").does_not_contain("FAILED")
    assert_that(npm[0].returncode).is_equal_to(0)
    # The empty entry never ran the pin, so it goes once npm has finished.
    assert_that(entry.exists()).is_false()


def test_lintro_lock_left_stale_by_a_delay_is_never_reclaimed_by_npm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lintro run delayed past npm's threshold keeps its lock; npm refuses.

    Sol's delayed-release reproduction from PR #3420: lintro is suspended
    in release for over 60 seconds (here, its lock is backdated instead),
    and npm then tries to reclaim the lock. npm must not get it, so lintro's
    release can never remove a lock npm took.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Places npm's attempt inside lintro's release.
    """
    entry = tmp_path / "entry"
    entry.mkdir()
    lock = entry / "concurrency.lock"
    marker = npx_cache._acquire(lock)
    attempts: list[str] = []

    def npm_tries_to_reclaim() -> None:
        _age(lock)
        output, _ = _npm_holds(lock, 100).communicate(timeout=30)
        attempts.append(output)

    fired = _race_on_first_touch(monkeypatch, lock, npm_tries_to_reclaim)
    npx_cache._release(marker)
    monkeypatch.undo()
    after, _ = _npm_holds(lock, 100).communicate(timeout=30)

    assert_that(fired).is_length(1)
    assert_that(attempts[0]).contains("FAILED:ENOTEMPTY").does_not_contain("HELD")
    assert_that(sorted(p.name for p in entry.iterdir())).is_empty()
    assert_that(after).contains("HELD", "NORMAL")


def test_npm_waits_for_a_lock_lintro_holds_then_takes_it(tmp_path: Path) -> None:
    """While lintro holds an entry's lock, real npm waits rather than failing.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    entry = tmp_path / "entry"
    entry.mkdir()
    lock = entry / "concurrency.lock"
    marker = npx_cache._acquire(lock)
    npm = _npm_holds(lock, 100)
    time.sleep(1.5)
    npm_waited = npm.poll() is None and npx_cache._holds(marker)
    npx_cache._release(marker)
    output, _ = npm.communicate(timeout=30)

    assert_that(npm_waited).is_true()
    assert_that(output).contains("HELD", "NORMAL")
    assert_that(lock.exists()).is_false()
