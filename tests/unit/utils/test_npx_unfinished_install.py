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
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.plugins import subprocess_executor
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.version_parsing import _run_probe, _run_probe_with_retry
from lintro.utils import npx_unfinished
from lintro.utils.npx_call import npx_shell_safe_command
from lintro.utils.npx_integrity import (
    NpxExit,
    NpxInstallError,
    NpxIntegrityError,
    cache_entries,
    install_unfinished,
)
from lintro.utils.npx_unfinished import bound_cache
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


def _half_install(cache: Path, *, bins: bool = False) -> str:
    """Return shell that leaves an entry the way a stopped npm install does.

    The package is unpacked and npm's lock is held, but ``package-lock.json``,
    which npm writes last, is not there. With *bins*, npm has also linked
    the executables, which it does before running install scripts: the
    shape a real install stopped during ``postinstall`` leaves.

    Args:
        cache: Per-pin npm cache.
        bins: Whether ``node_modules/.bin`` is already linked.

    Returns:
        Shell commands.
    """
    entry = cache / "_npx" / _ENTRY
    linked = (
        f"mkdir -p '{entry}/node_modules/.bin'\n"
        f"echo 'console.log(1)' > '{entry}/node_modules/prettier/bin.js'\n"
        f"ln -s ../prettier/bin.js '{entry}/node_modules/.bin/prettier'\n"
        if bins
        else ""
    )
    return (
        f"mkdir -p '{entry}/node_modules/prettier' '{entry}/concurrency.lock'\n"
        f"echo '{{}}' > '{entry}/node_modules/prettier/package.json'\n"
        f"{linked}"
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


def _first_call_half_installs(tmp_path: Path, cache: Path, then: str) -> list[str]:
    """Return a fake npx whose first call leaves a half install with bins.

    Every later call installs the complete entry and runs the tool.

    Args:
        tmp_path: Temporary directory provided by pytest.
        cache: Per-pin npm cache.
        then: Shell the first call runs after the half install.

    Returns:
        The fake npx argv.
    """
    staged = write_npx_entry(cache, [tool_package()], name=_ENTRY)
    complete = tmp_path / "complete"
    staged.rename(complete)
    claimed = tmp_path / "first"
    return _fake_npx(
        tmp_path / "bin",
        f"if mkdir '{claimed}' 2>/dev/null; then\n"
        f"{_half_install(cache, bins=True)}{then}"
        "fi\n"
        f"mv '{complete}' '{cache}/_npx/{_ENTRY}'\necho '[]'\n",
    )


#: :data:`_EXECUTORS`, with one version probe attempt instead of the probe's
#: retry, so the timeout reaches the test.
_SINGLE_ATTEMPT: dict[str, Callable[[list[str], float], object]] = {
    **_EXECUTORS,
    "version_probe": lambda argv, timeout: _run_probe(
        [*argv, "--version"],
        timeout=timeout,
        env=None,
        cwd=None,
    ),
}


@pytest.mark.parametrize("executor", list(_SINGLE_ATTEMPT), ids=list(_SINGLE_ATTEMPT))
def test_a_cold_install_stopped_after_linking_bins_does_not_block_the_next_run(
    cache: Path,
    tmp_path: Path,
    executor: str,
) -> None:
    """Stopped during postinstall: bins linked, no lockfile, npm's lock left.

    npx runs the tool only after writing the lockfile, so the tool never ran
    from this entry: lintro removes it with npm's lock, and the next run
    installs at once instead of waiting on that lock and then refusing it.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: Which lintro entry point spawns npx.
    """
    npx = _first_call_half_installs(tmp_path, cache, "exec sleep 30\n")

    with pytest.raises(subprocess.TimeoutExpired):
        _SINGLE_ATTEMPT[executor](_command(npx, cache), 1.0)
    assert_that(cache_entries(cache)).is_empty()

    started = time.monotonic()
    result = _SINGLE_ATTEMPT[executor](_command(npx, cache), 30.0)

    assert_that(time.monotonic() - started).is_less_than(10.0)
    assert_that(getattr(result, "returncode", None)).is_zero()
    assert_that([entry.name for entry in cache_entries(cache)]).is_equal_to([_ENTRY])


def test_a_failed_exit_after_bins_were_linked_keeps_the_entry(
    cache: Path,
    tmp_path: Path,
) -> None:
    """An npx that exited by itself may have run the tool: the entry stays.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
    """
    npx = _fake_npx(
        tmp_path / "bin",
        f"{_half_install(cache, bins=True)}"
        f"rmdir '{cache}/_npx/{_ENTRY}/concurrency.lock'\n"
        "echo '[]'\nexit 1\n",
    )

    with pytest.raises(NpxIntegrityError) as caught:
        run_subprocess(_command(npx, cache), timeout=30)

    assert_that(isinstance(caught.value, NpxInstallError)).is_false()
    assert_that(str(caught.value)).contains("cannot be trusted")
    assert_that(caught.value.after_run).is_true()
    assert_that([entry.name for entry in cache_entries(cache)]).is_equal_to([_ENTRY])


def _exit_with_bins_then_fail(cache: Path, npx_exit: NpxExit) -> None:
    """Leave linked bins and no lockfile, record a failed exit, then raise.

    Args:
        cache: Per-pin npm cache.
        npx_exit: Where the exit status is recorded.

    Raises:
        RuntimeError: Always, as lintro failing after npx exited.
    """
    bin_dir = cache / "_npx" / _ENTRY / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "prettier").write_text("", encoding="utf-8")
    npx_exit.record(1, "")
    raise RuntimeError("after npx exited")


def test_an_error_after_npx_exited_by_itself_keeps_an_entry_with_bins(
    cache: Path,
) -> None:
    """Once npx has exited with a status, an exception does not widen cleanup.

    The exit status is recorded, so npx was not stopped: an entry with
    executables and no lockfile may have run the tool, and stays.

    Args:
        cache: Per-pin npm cache.
    """
    npx_exit = NpxExit()
    command = _command(["npx"], cache)

    with pytest.raises(RuntimeError):
        with npx_shell_safe_command(command, timeout=30.0, npx_exit=npx_exit):
            _exit_with_bins_then_fail(cache, npx_exit)

    assert_that([entry.name for entry in cache_entries(cache)]).is_equal_to([_ENTRY])


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
#: timeout, what it raises, and whether its executables were linked.
_FIRST_RUN_STOPS: dict[str, tuple[str, float, type[BaseException], bool]] = {
    "times-out": ("exec sleep 30\n", 3.0, subprocess.TimeoutExpired, False),
    # Stopped during postinstall: bins linked, lockfile not yet written,
    # npm's lock left behind.
    "times-out-after-linking-bins": (
        "exec sleep 30\n",
        3.0,
        subprocess.TimeoutExpired,
        True,
    ),
    "fails": (
        "{wait_go}rmdir '{lock}'\necho 'npm error code E500' >&2\nexit 1\n",
        30.0,
        NpxInstallError,
        False,
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
    body, first_timeout, first_error, bins = _FIRST_RUN_STOPS[stop]
    staged = write_npx_entry(cache, [tool_package()], name=_ENTRY)
    complete = tmp_path / "complete"
    staged.rename(complete)
    started, go, claimed = tmp_path / "started", tmp_path / "go", tmp_path / "first"
    wait_go = f"while [ ! -e '{go}' ]; do sleep 0.02; done\n"
    npx = _fake_npx(
        tmp_path / "bin",
        f"if mkdir '{claimed}' 2>/dev/null; then\n"
        f"{_half_install(cache, bins=bins)}touch '{started}'\n"
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


@pytest.mark.skipif(
    not sys.platform.startswith(("linux", "darwin")),
    reason="Linux and macOS both offer descriptor-relative calls",
)
def test_posix_cleanup_works_through_the_open_npx_directory(cache: Path) -> None:
    """Where the calls exist, ``_npx`` is held open and cleanup goes through it.

    ``os.replace`` never appears in ``os.supports_dir_fd``; testing for it
    once switched this off on every POSIX system.

    Args:
        cache: Per-pin npm cache.
    """
    assert_that(npx_unfinished._DIR_FD_OPS).is_true()
    with bound_cache(cache) as bound:
        assert bound is not None
        held = bound._fd
        assert held is not None
        assert_that(os.fstat(held).st_ino).is_equal_to(
            os.stat(cache / "_npx").st_ino,
        )


def test_cleanup_stays_in_the_examined_cache_when_it_is_swapped_after_the_check(
    cache: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A swap after the identity check is not followed: the descriptor is.

    The cache directory is replaced by another one holding an unfinished
    entry of the same name between the identity comparison and the removal.
    Removing by path would delete that entry; removing through the open
    ``_npx`` removes the one npx was writing, wherever it now is.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Swaps the cache in the middle of the cleanup.
    """
    other = tmp_path / "elsewhere"
    planted = other / "_npx" / _ENTRY / "node_modules" / "prettier"
    planted.mkdir(parents=True)
    (planted / "package.json").write_text("{}", encoding="utf-8")
    moved = Path(f"{cache}.moved")
    check = install_unfinished

    def swap_then_check(entry: Path, **kwargs: Any) -> bool:
        if not moved.exists():
            cache.rename(moved)
            other.rename(cache)
        return check(entry, **kwargs)

    monkeypatch.setattr(npx_unfinished, "install_unfinished", swap_then_check)
    npx = _fake_npx(tmp_path / "bin", f"{_half_install(cache)}exec sleep 30\n")

    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess(_command(npx, cache), timeout=1.0)

    entry = cache / "_npx" / _ENTRY
    assert_that(str(entry / "node_modules" / "prettier" / "package.json")).exists()
    assert_that(cache_entries(moved)).is_empty()


def _gone(pid: int, budget: float = 10.0) -> bool:
    """Report whether *pid* has exited and been reaped within *budget*.

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


@pytest.mark.parametrize("executor", list(_SINGLE_ATTEMPT), ids=list(_SINGLE_ATTEMPT))
def test_a_stopped_install_takes_its_install_scripts_with_it(
    cache: Path,
    tmp_path: Path,
    executor: str,
) -> None:
    """The postinstall npm runs under npx stops writing when npx is stopped.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: Which lintro entry point spawns npx.
    """
    log, pid_file = tmp_path / "postinstall.log", tmp_path / "postinstall.pid"
    npx = _fake_npx(
        tmp_path / "bin",
        f"{_half_install(cache, bins=True)}"
        f"( while :; do echo x >> '{log}'; sleep 0.02; done ) &\n"
        f"echo $! > '{pid_file}.tmp'\nmv '{pid_file}.tmp' '{pid_file}'\n"
        "exec sleep 30\n",
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _SINGLE_ATTEMPT[executor](_command(npx, cache), 2.0)

        assert_that(_gone(int(pid_file.read_text(encoding="utf-8")))).is_true()
        size = log.stat().st_size
        time.sleep(0.5)
        assert_that(log.stat().st_size).is_equal_to(size)
        assert_that(cache_entries(cache)).is_empty()
    finally:
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
