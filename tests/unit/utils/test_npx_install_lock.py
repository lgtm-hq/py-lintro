"""lintro's per-pin install lock around npx's first install (#3421).

A cold pin is installed under an exclusive OS lock held until the run has
been checked; a warm pin runs without it; the cache gate never waits for it.
Real-npx coverage, two ``lintro chk`` processes on one cold cache, is in
``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import errno
import math
import os
import subprocess  # nosec B404 - runs this interpreter with a fixed argv; shell=False
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core.npx_cache import (
    NpxCacheLockError,
    npx_cache_dir,
    repair_npx_cache,
)
from lintro.utils import file_cache, npx_call, npx_install_lock
from lintro.utils.npx_call import npx_shell_safe_command
from lintro.utils.npx_install_lock import (
    NpxInstallLockError,
    NpxInstallLockTimeoutError,
    install_lock,
    install_lock_path,
    shared_install_lock_now,
)
from lintro.utils.npx_integrity import CacheState, NpxIntegrityError, verified_cache
from tests.npx_entries import tool_package, write_npx_entry

_SPEC = "prettier@3.9.4"


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Lintro's per-pin cache for prettier, under a private cache root and HOME.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped override of lintro's cache root and ``HOME``.

    Returns:
        The per-pin cache directory, resolved, not yet created.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        file_cache,
        "CACHE_DIR",
        Path(os.path.realpath(tmp_path)) / "lintro-cache",
    )
    return npx_cache_dir(_SPEC)


def _command(cache: Path) -> list[str]:
    """Return lintro's npx fallback shape for *cache*.

    Args:
        cache: Per-pin npm cache.

    Returns:
        The npx command.
    """
    return ["npx", "--yes", f"--cache={cache}", "--package", _SPEC, "prettier"]


def _exclusive_free(cache: Path) -> bool:
    """Report whether the install lock can be taken exclusively right now.

    Args:
        cache: Per-pin npm cache.

    Returns:
        Whether no other holder, shared or exclusive, has the lock.
    """
    try:
        with install_lock(cache, shared=False, deadline=time.monotonic()):
            return True
    except NpxInstallLockTimeoutError:
        return False


def test_cold_run_fails_when_another_run_keeps_installing(cache: Path) -> None:
    """A first install that outlasts the tool's timeout fails the run.

    The refusal is an ``NpxIntegrityError``, which the executors report as an
    execution failure with incomplete findings, never as a skip.

    Args:
        cache: Per-pin npm cache, cold.
    """
    started = time.monotonic()
    with (
        install_lock(cache, shared=False, deadline=math.inf),
        pytest.raises(NpxInstallLockTimeoutError) as caught,
        npx_shell_safe_command(_command(cache), timeout=0.3),
    ):
        pytest.fail("npx would have spawned beside another run's install")

    assert_that(time.monotonic() - started).is_less_than(5)
    assert_that(caught.value).is_instance_of(NpxIntegrityError)
    assert_that(str(caught.value)).contains(
        "still installing",
        str(install_lock_path(cache)),
        "findings are incomplete",
    )


def _raise_in_the_run(cache: Path) -> None:
    raise RuntimeError("the tool's executor failed")


def _install_tampered_entry(cache: Path) -> None:
    entry = write_npx_entry(cache, [tool_package()])
    bin_js = entry / "node_modules" / "prettier" / "bin.js"
    bin_js.write_text("console.log('[]');\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("during_run", "error"),
    [
        pytest.param(_raise_in_the_run, RuntimeError, id="run-raises"),
        pytest.param(_install_tampered_entry, NpxIntegrityError, id="check-refuses"),
    ],
)
def test_cold_run_holds_the_lock_until_it_ends_and_releases_it_on_error(
    cache: Path,
    during_run: Callable[[Path], None],
    error: type[Exception],
) -> None:
    """The first install excludes other runs, and an error never leaks the lock.

    Args:
        cache: Per-pin npm cache, cold.
        during_run: What happens while npx runs.
        error: What the run raises.
    """
    held_during_run: list[bool] = []
    with (
        pytest.raises(error),
        npx_shell_safe_command(_command(cache), timeout=5),
    ):
        with shared_install_lock_now(cache) as other_run_got_it:
            held_during_run.append(not other_run_got_it)
        during_run(cache)

    assert_that(held_during_run).is_equal_to([True])
    assert_that(_exclusive_free(cache)).is_true()


def test_warm_run_spawns_without_holding_the_lock(cache: Path) -> None:
    """A verified entry runs without the lock, so warm runs never queue.

    Args:
        cache: Per-pin npm cache.
    """
    write_npx_entry(cache, [tool_package()])

    with npx_shell_safe_command(_command(cache), timeout=5):
        free_during_run = _exclusive_free(cache)

    assert_that(free_during_run).is_true()


def test_run_that_finds_the_pin_installed_after_waiting_runs_warm(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that waited for another run's install releases the lock to run.

    The other run installs the pin between this run's first check, which
    finds no entry, and its second check under the exclusive lock.

    Args:
        cache: Per-pin npm cache, cold.
        monkeypatch: Scoped override of the check npx_call runs.
    """

    def install_after_first_check(target: Path, *, timeout: float) -> CacheState:
        state = verified_cache(target, timeout=timeout)
        if not state.entries:
            write_npx_entry(cache, [tool_package()])
        return state

    monkeypatch.setattr(npx_call, "verified_cache", install_after_first_check)

    with npx_shell_safe_command(_command(cache), timeout=5):
        free_during_run = _exclusive_free(cache)

    assert_that(free_during_run).is_true()


def test_cache_gate_leaves_an_install_in_progress_alone(cache: Path) -> None:
    """The gate hands off instead of removing npx's half-written entry.

    This was the failure seen with concurrent cold runs: one run's gate moved
    aside the entry another run's npx had created but not yet locked, and
    that npx failed with ``ENOENT`` on ``concurrency.lock``.

    Args:
        cache: Per-pin npm cache.
    """
    half_written = cache / "_npx" / "0123456789abcdef"
    half_written.mkdir(parents=True)

    with (
        install_lock(cache, shared=False, deadline=math.inf),
        pytest.raises(NpxCacheLockError, match="installing the pin"),
    ):
        repair_npx_cache(
            cache,
            package="prettier",
            pin="3.9.4",
            binary="prettier",
            pathsep=":",
            lock_wait=0.0,
        )

    assert_that(sorted(path.name for path in (cache / "_npx").iterdir())).is_equal_to(
        ["0123456789abcdef"],
    )


#: Takes the install lock exclusively, says so, then waits to be killed.
_HOLDER = """
import math, sys, time
from pathlib import Path
from lintro.utils.npx_install_lock import install_lock
with install_lock(Path(sys.argv[1]), shared=False, deadline=math.inf):
    print("HELD", flush=True)
    time.sleep(120)
"""


def test_lock_of_a_killed_install_never_blocks_later_runs(cache: Path) -> None:
    """The OS drops the lock with its holder; nothing stale is left to clear.

    Args:
        cache: Per-pin npm cache.
    """
    holder = subprocess.Popen(  # nosec B603 - this interpreter, fixed argv
        [sys.executable, "-c", _HOLDER, str(cache)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert_that(holder.stdout.readline().strip()).is_equal_to("HELD")
        assert_that(_exclusive_free(cache)).is_false()
    finally:
        holder.kill()
        holder.communicate(timeout=30)

    with install_lock(cache, shared=False, deadline=time.monotonic() + 5):
        pass
    assert_that(install_lock_path(cache).is_file()).is_true()


def _lock_is_a_directory(cache: Path, tmp_path: Path) -> str:
    install_lock_path(cache).mkdir(parents=True)
    return "it is a directory"


def _lock_is_a_symlink(cache: Path, tmp_path: Path) -> str:
    target = tmp_path / "elsewhere.lock"
    target.write_text("not lintro's", encoding="utf-8")
    install_lock_path(cache).parent.mkdir(parents=True)
    install_lock_path(cache).symlink_to(target)
    return "symbolic link"


def _lock_is_a_dangling_symlink(cache: Path, tmp_path: Path) -> str:
    install_lock_path(cache).parent.mkdir(parents=True)
    install_lock_path(cache).symlink_to(tmp_path / "never-created.lock")
    return "symbolic link"


def _lock_directory_is_unwritable(cache: Path, tmp_path: Path) -> str:
    directory = install_lock_path(cache).parent
    directory.mkdir(parents=True)
    directory.chmod(0o555)
    return "Permission denied"


_UNUSABLE = [
    pytest.param(_lock_is_a_directory, id="directory"),
    pytest.param(_lock_is_a_symlink, id="symlink"),
    pytest.param(_lock_is_a_dangling_symlink, id="dangling-symlink"),
    pytest.param(
        _lock_directory_is_unwritable,
        id="unwritable-directory",
        marks=pytest.mark.skipif(
            sys.platform == "win32" or os.geteuid() == 0,
            reason="POSIX permissions that bind the running user",
        ),
    ),
]


@pytest.mark.parametrize("hazard", _UNUSABLE)
def test_unusable_lock_fails_the_run_naming_the_lock(
    cache: Path,
    tmp_path: Path,
    hazard: Callable[[Path, Path], str],
) -> None:
    """A lock lintro cannot use is an execution failure before npx starts.

    It is never followed through a link, so nothing outside lintro's cache
    is created or locked.

    Args:
        cache: Per-pin npm cache, cold.
        tmp_path: Temporary directory provided by pytest.
        hazard: Puts the hazard in place and returns the reason expected.
    """
    reason = hazard(cache, tmp_path)
    try:
        with (
            pytest.raises(NpxIntegrityError) as caught,
            npx_shell_safe_command(_command(cache), timeout=5),
        ):
            pytest.fail("npx would have spawned without a usable lock")
    finally:
        install_lock_path(cache).parent.chmod(0o755)

    assert_that(caught.value).is_instance_of(NpxInstallLockError)
    assert_that(str(caught.value)).contains(
        str(install_lock_path(cache)),
        reason,
        "findings are incomplete",
    )
    assert_that((tmp_path / "never-created.lock").exists()).is_false()
    if (tmp_path / "elsewhere.lock").exists():
        assert_that(
            (tmp_path / "elsewhere.lock").read_text(encoding="utf-8"),
        ).is_equal_to(
            "not lintro's",
        )


def test_filesystem_without_file_locks_fails_the_run(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``flock`` refused by the filesystem is a failure, never an unlocked run.

    Args:
        cache: Per-pin npm cache, cold.
        monkeypatch: Scoped stand-in for a filesystem without ``flock``.
    """
    fcntl = pytest.importorskip("fcntl")

    def unsupported(fd: int, operation: int) -> None:
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP))

    monkeypatch.setattr(fcntl, "flock", unsupported)

    with (
        pytest.raises(
            NpxInstallLockError,
            match="does not support file locks",
        ) as caught,
        npx_shell_safe_command(_command(cache), timeout=5),
    ):
        pytest.fail("npx would have spawned unlocked")

    assert_that(str(caught.value)).contains(
        str(install_lock_path(cache)),
        f"Point lintro's cache {file_cache.CACHE_DIR}",
        "symbolic link",
    )
    assert_that(str(caught.value)).does_not_contain("regular file")


def test_cache_gate_hands_an_unusable_lock_to_the_spawn_check(cache: Path) -> None:
    """The gate does not report the tool unavailable over its lock.

    Args:
        cache: Per-pin npm cache.
    """
    entry = write_npx_entry(cache, [tool_package()])
    install_lock_path(cache).mkdir()

    with pytest.raises(NpxCacheLockError, match="spawn-time check"):
        repair_npx_cache(
            cache,
            package="prettier",
            pin="3.9.4",
            binary="prettier",
            pathsep=":",
            lock_wait=0.0,
        )

    assert_that(entry.is_dir()).is_true()


def test_lock_replaced_before_npx_installs_fails_the_run(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock deleted and recreated while held is caught before npx starts.

    Args:
        cache: Per-pin npm cache, cold.
        monkeypatch: Scoped override of the check npx_call runs.
    """
    calls: list[Path] = []

    def replace_lock_on_second_check(target: Path, *, timeout: float) -> CacheState:
        calls.append(target)
        if len(calls) == 2:
            lock = install_lock_path(cache)
            lock.unlink()
            lock.write_bytes(b"")
        return verified_cache(target, timeout=timeout)

    monkeypatch.setattr(npx_call, "verified_cache", replace_lock_on_second_check)

    with (
        pytest.raises(NpxInstallLockError, match="deleted or replaced") as caught,
        npx_shell_safe_command(_command(cache), timeout=5),
    ):
        pytest.fail("npx would have installed without the lock")

    assert_that(caught.value).is_instance_of(NpxIntegrityError)
    assert_that(_exclusive_free(cache)).is_true()


#: Waits for the install lock, then writes argv[2] and holds it until killed.
_WAITER = """
import math, sys, time
from pathlib import Path
from lintro.utils.npx_install_lock import install_lock
with install_lock(Path(sys.argv[1]), shared=False, deadline=time.monotonic() + 60):
    Path(sys.argv[2]).write_text("held")
    time.sleep(120)
"""


def _wait_until(condition: Callable[[], bool], seconds: float) -> bool:
    until = time.monotonic() + seconds
    while not condition():
        if time.monotonic() >= until:
            return False
        time.sleep(0.05)
    return True


def test_waiter_on_a_deleted_lock_waits_for_the_new_holder(
    cache: Path,
    tmp_path: Path,
) -> None:
    """A run waiting on a lock file that was deleted never shares the lock.

    Run 1 holds the lock; run 2 waits on that file; the file is deleted and
    run 3 takes a lock on the new one. When run 1 exits, run 2 must not take
    the orphaned file's lock while run 3 holds the real one.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
    """
    marker = tmp_path / "waiter-held"
    started: list[subprocess.Popen[str]] = []
    try:
        first = subprocess.Popen(  # nosec B603 - this interpreter, fixed argv
            [sys.executable, "-c", _HOLDER, str(cache)],
            stdout=subprocess.PIPE,
            text=True,
        )
        started.append(first)
        assert first.stdout is not None
        assert_that(first.stdout.readline().strip()).is_equal_to("HELD")
        waiter = subprocess.Popen(  # nosec B603 - this interpreter, fixed argv
            [sys.executable, "-c", _WAITER, str(cache), str(marker)],
            stderr=subprocess.PIPE,
            text=True,
        )
        started.append(waiter)
        assert waiter.stderr is not None
        assert_that(waiter.stderr.readline()).contains("Waiting up to")
        install_lock_path(cache).unlink()
        third = subprocess.Popen(  # nosec B603 - this interpreter, fixed argv
            [sys.executable, "-c", _HOLDER, str(cache)],
            stdout=subprocess.PIPE,
            text=True,
        )
        started.append(third)
        assert third.stdout is not None
        assert_that(third.stdout.readline().strip()).is_equal_to("HELD")

        first.kill()
        shared_while_third_held = _wait_until(marker.exists, 3)
        third.kill()
        held_after_third = _wait_until(marker.exists, 10)
    finally:
        for process in started:
            process.kill()
            process.communicate(timeout=30)

    assert_that(shared_while_third_held).is_false()
    assert_that(held_after_third).is_true()


def test_lock_file_that_never_stays_in_place_is_retried_at_a_bounded_rate(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock that never matches its path pauses between tries and says why.

    Fable's round-2 reproduction: with the identity check always failing,
    the retry loop opened and locked the file tens of thousands of times a
    second for the whole timeout, then blamed another lintro run.

    Args:
        cache: Per-pin npm cache.
        monkeypatch: Scoped stand-in for a lock that keeps being replaced.
    """
    opened: list[Path] = []
    real_open = npx_install_lock._open
    real_is_at_path = npx_install_lock._is_at_path

    def counting_open(path: Path) -> int:
        opened.append(path)
        return real_open(path)

    monkeypatch.setattr(npx_install_lock, "_open", counting_open)
    monkeypatch.setattr(npx_install_lock, "_is_at_path", lambda fd, path: False)
    started = time.monotonic()

    with (
        pytest.raises(NpxInstallLockError, match="kept being replaced") as caught,
        install_lock(cache, shared=False, deadline=started + 0.5),
    ):
        pytest.fail("a lock that is not the file at its path was held")

    assert_that(len(opened)).is_between(2, 20)
    assert_that(isinstance(caught.value, NpxInstallLockTimeoutError)).is_false()
    assert_that(str(caught.value)).does_not_contain("still installing")
    monkeypatch.setattr(npx_install_lock, "_is_at_path", real_is_at_path)
    assert_that(_exclusive_free(cache)).is_true()
