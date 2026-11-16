"""lintro's per-pin install lock around npx's first install (#3421).

A cold pin is installed under an exclusive OS lock held until the run has
been checked; a warm pin runs without it; the cache gate never waits for it.
Real-npx coverage, two ``lintro chk`` processes on one cold cache, is in
``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

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
from lintro.utils import file_cache
from lintro.utils.npx_call import npx_shell_safe_command
from lintro.utils.npx_install_lock import (
    NpxInstallLockTimeoutError,
    install_lock,
    install_lock_path,
    shared_install_lock_now,
)
from lintro.utils.npx_integrity import NpxIntegrityError
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
