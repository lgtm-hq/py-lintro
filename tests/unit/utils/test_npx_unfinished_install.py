"""A cold npx install that did not finish is not tampering (#3530).

npx is replaced by a shell script that writes into lintro's per-pin cache the
way npm does, so nothing is downloaded. A run that times out mid-install is a
timeout, a run whose npx exits with an error mid-install is an install
failure, and either way the half-written entry is removed under lintro's
exclusive install lock, so a run waiting for that lock installs afresh. An
entry with no lockfile that npx did not explain is still refused.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - TimeoutExpired only; scripts run via the executors
import sys
import threading
import time
import types
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.plugins import subprocess_executor
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.version_parsing import _run_probe_with_retry
from lintro.utils.npx_integrity import (
    NpxInstallError,
    NpxIntegrityError,
    cache_entries,
)
from tests.npx_entries import tool_package, write_npx_entry

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake npx is a POSIX shell script",
)

_SPEC = "prettier@3.9.4"
_ENTRY = "0123456789abcdef"

#: Executors that spawn npx, each called as ``(argv, timeout) -> result``.
_EXECUTORS: dict[str, Callable[[list[str], float], object]] = {
    "run_subprocess": lambda argv, timeout: run_subprocess(argv, timeout=timeout),
    "run_subprocess_streaming": lambda argv, timeout: run_subprocess_streaming(
        argv,
        timeout=timeout,
    ),
    "version_probe": lambda argv, timeout: _run_probe_with_retry(
        [*argv, "--version"],
        timeout,
    ),
}


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real ``~/.node_modules`` out of the ancestor check.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped ``HOME`` override.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """Per-pin npm cache, resolved, with nothing in it yet.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Returns:
        The cache directory.
    """
    return Path(os.path.realpath(tmp_path)) / "npx" / _SPEC


def _fake_npx(directory: Path, body: str) -> list[str]:
    """Write a fake npx and return lintro's npx fallback command for it.

    Args:
        directory: Where the script goes.
        body: Shell script body.

    Returns:
        The argv, without the per-pin cache option, which callers add.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "npx"
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return [str(path)]


def _command(npx: list[str], cache: Path) -> list[str]:
    """Return lintro's npx fallback shape for *cache*.

    Args:
        npx: The fake npx argv.
        cache: Per-pin npm cache.

    Returns:
        The npx command.
    """
    return [*npx, "--yes", f"--cache={cache}", "--package", _SPEC, "prettier"]


def _half_install(cache: Path) -> str:
    """Return shell that leaves an entry the way a stopped npm install does.

    The package is unpacked and npm's lock is held, but ``package-lock.json``,
    which npm writes last, is not there.

    Args:
        cache: Per-pin npm cache.

    Returns:
        Shell commands.
    """
    entry = cache / "_npx" / _ENTRY
    return (
        f"mkdir -p '{entry}/node_modules/prettier' '{entry}/concurrency.lock'\n"
        f"echo '{{}}' > '{entry}/node_modules/prettier/package.json'\n"
    )


@pytest.mark.parametrize("executor", list(_EXECUTORS), ids=list(_EXECUTORS))
def test_a_cold_install_that_times_out_is_a_timeout(
    cache: Path,
    tmp_path: Path,
    executor: str,
) -> None:
    """Npx killed at the timeout mid-install: a timeout, and no entry is left.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: Which lintro entry point spawns npx.
    """
    npx = _fake_npx(tmp_path / "bin", f"{_half_install(cache)}exec sleep 30\n")

    with pytest.raises(subprocess.TimeoutExpired):
        _EXECUTORS[executor](_command(npx, cache), 1.0)

    assert_that(cache_entries(cache)).is_empty()


@pytest.mark.parametrize("executor", list(_EXECUTORS), ids=list(_EXECUTORS))
def test_a_failed_cold_install_is_an_install_failure(
    cache: Path,
    tmp_path: Path,
    executor: str,
) -> None:
    """Npx exits with an error mid-install: its own error, not tampering.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: Which lintro entry point spawns npx.
    """
    npx = _fake_npx(
        tmp_path / "bin",
        f"{_half_install(cache)}"
        f"rmdir '{cache}/_npx/{_ENTRY}/concurrency.lock'\n"
        "echo 'npm error code ECONNRESET' >&2\nexit 1\n",
    )

    with pytest.raises(NpxInstallError) as caught:
        _EXECUTORS[executor](_command(npx, cache), 30.0)

    assert_that(str(caught.value)).contains("exited with code 1").contains(
        "ECONNRESET",
    ).does_not_contain("cannot be trusted")
    assert_that(caught.value.after_run).is_false()
    assert_that(cache_entries(cache)).is_empty()


#: An entry npx did not explain, by how npx exited and what it left.
_UNEXPLAINED: dict[str, tuple[int, Callable[[Path], object], str]] = {
    "clean-exit-without-lockfile": (
        0,
        lambda entry: (entry / "package-lock.json").unlink(),
        "has no readable package-lock.json",
    ),
    # npx's status is the tool's too: a linter that finds issues exits 1. An
    # entry whose install got as far as its executables, and then lost its
    # lockfile, is tampering, not an install that failed.
    "failed-exit-after-the-lockfile-was-deleted": (
        1,
        lambda entry: (entry / "package-lock.json").unlink(),
        "has no readable package-lock.json",
    ),
    "failed-exit-with-a-lockfile-that-lies": (
        1,
        lambda entry: (entry / "node_modules" / "prettier" / "bin.js").write_text(
            "console.log('[]');\n",
            encoding="utf-8",
        ),
        "bin.js changed",
    ),
}


@pytest.mark.parametrize("case", list(_UNEXPLAINED), ids=list(_UNEXPLAINED))
def test_an_entry_npx_did_not_explain_is_still_refused(
    cache: Path,
    tmp_path: Path,
    case: str,
) -> None:
    """Only a failed exit with no lockfile at all reads as an install failure.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        case: How npx exits and what its entry holds.
    """
    code, damage, expected = _UNEXPLAINED[case]
    staged = write_npx_entry(cache, [tool_package()], name=_ENTRY)
    damage(staged)
    aside = tmp_path / "staged"
    staged.rename(aside)
    npx = _fake_npx(
        tmp_path / "bin",
        f"mv '{aside}' '{cache}/_npx/{_ENTRY}'\necho '[]'\nexit {code}\n",
    )

    with pytest.raises(NpxIntegrityError) as caught:
        run_subprocess(_command(npx, cache), timeout=30)

    assert_that(isinstance(caught.value, NpxInstallError)).is_false()
    assert_that(str(caught.value)).contains(expected).contains("cannot be trusted")
    assert_that(caught.value.after_run).is_true()
    # The entry stays, as evidence, for the next run's refusal.
    assert_that([entry.name for entry in cache_entries(cache)]).is_equal_to([_ENTRY])


def _wait_for(path: Path, budget: float = 20.0) -> None:
    """Wait until *path* exists.

    Args:
        path: The marker.
        budget: Longest wait in seconds.
    """
    give_up = time.monotonic() + budget
    while not path.exists():
        assert_that(time.monotonic()).is_less_than(give_up)
        time.sleep(0.02)


#: How the first run's npx stops: its body after the half install, its
#: timeout, and what it raises.
_FIRST_RUN_STOPS: dict[str, tuple[str, float, type[BaseException]]] = {
    "times-out": ("exec sleep 30\n", 3.0, subprocess.TimeoutExpired),
    "fails": (
        "{wait_go}rmdir '{lock}'\necho 'npm error code E500' >&2\nexit 1\n",
        30.0,
        NpxInstallError,
    ),
}


@pytest.mark.parametrize("stop", list(_FIRST_RUN_STOPS), ids=list(_FIRST_RUN_STOPS))
def test_a_run_waiting_on_an_unfinished_cold_install_installs_afresh(
    cache: Path,
    tmp_path: Path,
    stop: str,
) -> None:
    """Concurrent cold runs: the second is not refused over the first's leftovers.

    The first run's npx starts a cold install and stops. A second run, started
    while the first holds lintro's exclusive install lock, waits for it, then
    finds a cold cache, installs, and its check passes.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        stop: How the first run's npx stops.
    """
    body, first_timeout, first_error = _FIRST_RUN_STOPS[stop]
    staged = write_npx_entry(cache, [tool_package()], name=_ENTRY)
    complete = tmp_path / "complete"
    staged.rename(complete)
    started, go, claimed = tmp_path / "started", tmp_path / "go", tmp_path / "first"
    wait_go = f"while [ ! -e '{go}' ]; do sleep 0.02; done\n"
    npx = _fake_npx(
        tmp_path / "bin",
        f"if mkdir '{claimed}' 2>/dev/null; then\n"
        f"{_half_install(cache)}touch '{started}'\n"
        f"{body.format(wait_go=wait_go, lock=cache / '_npx' / _ENTRY / 'concurrency.lock')}"
        "fi\n"
        f"mv '{complete}' '{cache}/_npx/{_ENTRY}'\necho '[]'\n",
    )
    outcomes: dict[str, object] = {}

    def run(name: str, timeout: float) -> None:
        try:
            outcomes[name] = run_subprocess(_command(npx, cache), timeout=timeout)
        except (subprocess.TimeoutExpired, NpxIntegrityError) as exc:
            outcomes[name] = exc

    first = threading.Thread(target=run, args=("first", first_timeout))
    first.start()
    _wait_for(started)
    second = threading.Thread(target=run, args=("second", 30.0))
    second.start()
    go.touch()
    first.join(timeout=60)
    second.join(timeout=60)

    assert_that(outcomes["first"]).is_instance_of(first_error)
    assert_that(outcomes["second"]).is_instance_of(SubprocessResult)
    second_result = outcomes["second"]
    assert isinstance(second_result, SubprocessResult)
    assert_that(second_result.returncode).is_zero()
    assert_that([entry.name for entry in cache_entries(cache)]).is_equal_to([_ENTRY])


#: How the cache directory is swapped out during a cold run: the shell that
#: does it, given the cache and a directory holding another unfinished
#: entry, and where that entry is afterwards.
_SWAPS: dict[str, tuple[str, str]] = {
    "replaced-by-a-link": (
        "mv '{cache}' '{cache}.moved'\nln -s '{other}' '{cache}'\n",
        "{other}",
    ),
    "replaced-by-another-directory": (
        "mv '{cache}' '{cache}.moved'\nmv '{other}' '{cache}'\n",
        "{cache}",
    ),
}


@pytest.mark.parametrize("swap", list(_SWAPS), ids=list(_SWAPS))
def test_cleanup_never_follows_a_cache_directory_swapped_during_the_run(
    cache: Path,
    tmp_path: Path,
    swap: str,
) -> None:
    """Only the cache directory examined before the spawn is cleaned up.

    The install lock sits beside the cache, so it does not notice the cache
    directory itself being replaced. The unfinished entry now at the cache's
    path is not the one npx was writing, and must not be removed.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        swap: How the cache directory is replaced.
    """
    other = tmp_path / "elsewhere"
    planted = other / "_npx" / _ENTRY / "node_modules" / "prettier"
    planted.mkdir(parents=True)
    (planted / "package.json").write_text("{}", encoding="utf-8")
    script, where = _SWAPS[swap]
    npx = _fake_npx(
        tmp_path / "bin",
        f"{_half_install(cache)}{script.format(cache=cache, other=other)}"
        "exec sleep 30\n",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess(_command(npx, cache), timeout=1.0)

    survivor = Path(where.format(cache=cache, other=other)) / "_npx" / _ENTRY
    assert_that(str(survivor / "node_modules" / "prettier" / "package.json")).exists()
    moved = Path(f"{cache}.moved") / "_npx" / _ENTRY
    assert_that(str(moved / "node_modules" / "prettier" / "package.json")).exists()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a directory whatever its mode",
)
def test_a_cleanup_that_cannot_read_the_cache_keeps_the_timeout(
    cache: Path,
    tmp_path: Path,
) -> None:
    """An error during cleanup never replaces the error that ended the run.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
    """
    npx = _fake_npx(
        tmp_path / "bin",
        f"{_half_install(cache)}chmod 000 '{cache}'\nexec sleep 30\n",
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_subprocess(_command(npx, cache), timeout=1.0)
    finally:
        cache.chmod(0o755)

    # Left for the next run, which can read the cache, to check.
    assert_that([entry.name for entry in cache_entries(cache)]).is_equal_to([_ENTRY])


#: How a warm run's npx treats the verified entry before it times out, and
#: what lintro raises.
_WARM_TIMEOUTS: dict[str, tuple[str, type[BaseException]]] = {
    "entry-untouched": ("", subprocess.TimeoutExpired),
    "entry-changed": (
        "printf 'console.log(1);\\n' > '{bin_js}'\n",
        NpxIntegrityError,
    ),
}


@pytest.mark.parametrize("executor", list(_EXECUTORS), ids=list(_EXECUTORS))
@pytest.mark.parametrize("case", list(_WARM_TIMEOUTS), ids=list(_WARM_TIMEOUTS))
def test_a_warm_run_that_times_out_still_checks_its_entry(
    cache: Path,
    tmp_path: Path,
    executor: str,
    case: str,
) -> None:
    """A change to a verified entry is reported even when the run timed out.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: Which lintro entry point spawns npx.
        case: What npx does to the entry before it hangs.
    """
    entry = write_npx_entry(cache, [tool_package()], name=_ENTRY)
    body, expected = _WARM_TIMEOUTS[case]
    bin_js = entry / "node_modules" / "prettier" / "bin.js"
    npx = _fake_npx(
        tmp_path / "bin",
        f"{body.format(bin_js=bin_js)}exec sleep 30\n",
    )

    with pytest.raises(expected) as caught:
        _EXECUTORS[executor](_command(npx, cache), 1.0)

    if isinstance(caught.value, NpxIntegrityError):
        assert_that(caught.value.after_run).is_true()
        assert_that(str(caught.value)).contains(str(entry)).contains(
            "TimeoutExpired",
        )
        assert_that(caught.value.__cause__).is_instance_of(subprocess.TimeoutExpired)
    assert_that([e.name for e in cache_entries(cache)]).is_equal_to([_ENTRY])


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


def test_an_interrupted_streaming_run_stops_npx_before_cleanup(
    cache: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C while streaming: npx is killed and reaped before its entry goes.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Interrupts the wait for npx's output.
    """
    pid_file = tmp_path / "npx.pid"
    npx = _fake_npx(
        tmp_path / "bin",
        f"{_half_install(cache)}echo $$ > '{pid_file}.tmp'\n"
        f"mv '{pid_file}.tmp' '{pid_file}'\nexec sleep 30\n",
    )

    class _Interrupted(threading.Thread):
        """A reader thread whose join is interrupted once npx is running."""

        def join(self, timeout: float | None = None) -> None:
            """Wait for npx to start, then raise as Ctrl-C would.

            Args:
                timeout: Ignored.

            Raises:
                KeyboardInterrupt: Always.
            """
            _wait_for(pid_file)
            raise KeyboardInterrupt

    monkeypatch.setattr(
        subprocess_executor,
        "threading",
        types.SimpleNamespace(Thread=_Interrupted),
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            run_subprocess_streaming(_command(npx, cache), timeout=30.0)
        pid = int(pid_file.read_text(encoding="utf-8"))
        assert_that(_alive(pid)).is_false()
        assert_that(cache_entries(cache)).is_empty()
    finally:
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
