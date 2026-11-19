"""Stop a child process and reap it before lintro moves on (#3530).

After an npx run that did not finish, lintro removes the half-written install
and lets go of its exclusive install lock. That is only safe once nothing npx
started can write to the cache, so when lintro itself ends the run (a
timeout, or any exception while npx is still running) it first kills npx and
what it started and waits for npx to exit.

npx runs in lintro's process group, exactly as any other tool, so a Ctrl-C
at the terminal reaches npx and its children directly. On POSIX the
processes npx started are found in the process table and killed one by one
(:func:`~lintro.utils.process_tree.kill_tree`). On Windows ``npx`` is
``npx.cmd``: killing the process lintro started ends ``cmd.exe`` only, so
the whole tree is ended with ``taskkill /T`` first, while the process handle
lintro holds keeps its process id from being reused. A process ``taskkill``
cannot see as part of the tree (one whose parent already exited) is not
ended.

A repeated Ctrl-C does not cut that sequence short: it is caught, the
sequence is completed, and the first interruption is raised afterwards
(:func:`finish_uninterrupted`). No signal handler is installed.
"""

from __future__ import annotations

import contextlib
import os
import subprocess  # nosec B404 - argv lists only, shell=False
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any, TypeVar

from lintro.utils.process_tree import TableReader, kill_tree, read_process_table

#: Seconds ``taskkill`` may take to end a process tree.
_TASKKILL_SECONDS: float = 10.0

#: Seconds spent collecting output a killed process left in its pipes.
_DRAIN_SECONDS: float = 1.0

#: Runs ``taskkill``; replaceable so the call can be observed off Windows.
Runner = Callable[..., object]

_T = TypeVar("_T")


def _repeat_until_done(step: Callable[[], _T]) -> _T:
    """Run *step* again after each Ctrl-C until it finishes.

    Args:
        step: The step to run.

    Returns:
        What *step* returned.
    """
    while True:
        try:
            return step()
        except KeyboardInterrupt:
            continue


def finish_uninterrupted(
    step: Callable[[], _T],
    *,
    pending: BaseException | None = None,
) -> _T:
    """Run *step* to the end, starting it again after each Ctrl-C.

    For a step that must not be left half done and can be repeated, such as
    stopping npx or removing its unfinished install. Another exception
    from *step* propagates at once.

    Args:
        step: The step to run.
        pending: The exception being handled when *step* runs, if any.

    Returns:
        What *step* returned.

    Raises:
        KeyboardInterrupt: The first one that arrived during *step*, once
            it has finished, unless *pending* is already one: then the
            caller re-raises *pending*.
    """
    try:
        return step()
    except KeyboardInterrupt:
        result = _repeat_until_done(step)
        if isinstance(pending, KeyboardInterrupt):
            return result
        raise


def _taskkill() -> str:
    r"""Return ``taskkill``'s full path, so ``PATH`` cannot substitute it.

    Returns:
        ``%SystemRoot%\System32\taskkill.exe``.
    """
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return str(Path(root) / "System32" / "taskkill.exe")


def stop_process(
    process: subprocess.Popen[Any],
    *,
    windows: bool | None = None,
    run: Runner = subprocess.run,
    tree: bool = False,
    wait_seconds: float | None = None,
    pending: BaseException | None = None,
    read_table: TableReader = read_process_table,
) -> None:
    """Kill *process*, and with *tree* what it started, then wait for it.

    A Ctrl-C during this is held until it is done
    (:func:`finish_uninterrupted`).

    Args:
        process: The child, which may already have exited; then nothing is
            signalled, since its id may belong to another process by now.
        windows: Whether this is Windows; defaults to ``os.name == "nt"``.
        run: What runs ``taskkill``.
        tree: Whether to find and kill the processes *process* started on
            POSIX; Windows always ends the whole tree.
        wait_seconds: How long to wait for it to exit, or None to wait until
            it has. Cleanup that must not run beneath a live process needs
            None; anything else may give up.
        pending: The exception being handled, as for
            :func:`finish_uninterrupted`.
        read_table: What reads the process table on POSIX.
    """
    on_windows = os.name == "nt" if windows is None else windows

    def stop() -> None:
        if process.poll() is not None:
            return  # Exited, and poll() has reaped it.
        if on_windows:
            # The handle Popen holds keeps this id from being reused.
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                run(  # nosec B603 - fixed absolute path, argv list
                    [_taskkill(), "/F", "/T", "/PID", str(process.pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=_TASKKILL_SECONDS,
                    check=False,
                )
        elif tree:
            kill_tree(process, read_table=read_table)
        with contextlib.suppress(OSError):
            process.kill()
        if wait_seconds is None:
            process.wait()
            return
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=wait_seconds)

    finish_uninterrupted(stop, pending=pending)


def run_reaped(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | IO[Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *argv* to completion like ``subprocess.run`` with captured text.

    The child is spawned as ``subprocess.run`` spawns it, in lintro's
    process group. Unlike ``subprocess.run``, which kills only the child and
    waits only a quarter of a second for it after a ``KeyboardInterrupt``,
    any exception first stops the child and what it started and reaps the
    child (:func:`stop_process`).

    Args:
        argv: The command; run with ``shell=False``.
        timeout: Seconds before the child is killed.
        cwd: Working directory.
        env: Environment, or None to inherit.
        stdin: Standard input, or None to inherit.

    Returns:
        The finished process, with its stdout and stderr.

    Raises:
        subprocess.TimeoutExpired: The child ran past *timeout*; it carries
            the output collected so far.
        BaseException: Whatever else ended the run, once the child has been
            stopped and reaped.
    """
    with subprocess.Popen(  # nosec B603 - args list, shell=False
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stop_process(process, tree=True)
            output: str | bytes | None
            errors: str | bytes | None
            try:
                output, errors = process.communicate(timeout=_DRAIN_SECONDS)
            except subprocess.TimeoutExpired as drained:
                # A process that escaped still holds the pipes open.
                output, errors = drained.output, drained.stderr
            raise subprocess.TimeoutExpired(
                list(argv),
                timeout,
                output=output,
                stderr=errors,
            ) from exc
        except BaseException as exc:
            stop_process(process, tree=True, pending=exc)
            raise
    return subprocess.CompletedProcess(
        list(argv),
        process.returncode,
        stdout,
        stderr,
    )
