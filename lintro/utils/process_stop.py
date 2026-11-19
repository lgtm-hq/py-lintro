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
(:func:`finish_uninterrupted`). No signal handler is installed. That wait is
bounded once the user has pressed Ctrl-C, so a child stuck in the kernel
(for example on a hung network home) cannot keep lintro from exiting: when
the child has not exited :data:`_STUCK_SECONDS` after its ``SIGKILL``, or at
the :data:`_ESCAPE_INTERRUPTS`-th Ctrl-C, lintro stops waiting and raises
:class:`ChildStillRunningError`. The caller must then leave everything the child
may still touch as it is, its install lock included, for process exit to
release.
"""

from __future__ import annotations

import contextlib
import os
import subprocess  # nosec B404 - argv lists only, shell=False
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, TypeVar

from loguru import logger

from lintro.utils.process_tree import ProcessTable, kill_tree

#: Seconds ``taskkill`` may take to end a process tree.
_TASKKILL_SECONDS: float = 10.0

#: Seconds spent collecting output a killed process left in its pipes.
_DRAIN_SECONDS: float = 1.0

#: Seconds lintro, once interrupted, waits for a child it sent ``SIGKILL``.
_STUCK_SECONDS: float = 10.0

#: The Ctrl-C, counting the one being handled, that ends that wait at once.
_ESCAPE_INTERRUPTS: int = 3


class ChildStillRunningError(KeyboardInterrupt):
    """Ctrl-C ended lintro's wait for a child that ``SIGKILL`` did not end.

    The child is not reaped and may still be writing. Whoever cleans up after
    it must leave that alone, and must not release a lock that keeps other
    runs away from what it writes: process exit releases it.

    Attributes:
        pid: The child.
    """

    pid: int

    def __init__(self, pid: int) -> None:
        """Name the child.

        Args:
            pid: The child.
        """
        super().__init__(
            f"process {pid} had not exited after SIGKILL when lintro was "
            "interrupted; lintro stopped waiting for it",
        )
        self.pid = pid


class _StuckError(Exception):
    """The interrupted wait for a killed child ran out."""


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
    table: ProcessTable | None = None,
) -> None:
    """Kill *process*, and with *tree* what it started, then wait for it.

    A Ctrl-C during this is held until it is done, as by
    :func:`finish_uninterrupted`, but not forever: once lintro has been
    interrupted (*pending* is a ``KeyboardInterrupt``, or one arrives now),
    it waits at most :data:`_STUCK_SECONDS` after the ``SIGKILL``, and the
    :data:`_ESCAPE_INTERRUPTS`-th Ctrl-C ends the wait at once.

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
        table: Where process identities come from on POSIX; this system's
            by default.

    Raises:
        KeyboardInterrupt: The first Ctrl-C that arrived during the stop,
            once the child has exited, unless *pending* is already one. When
            lintro stopped waiting for a child that had not exited, it is a
            :class:`ChildStillRunningError`, and the child is not reaped.
    """
    stopper = _Stopper(
        process,
        windows=os.name == "nt" if windows is None else windows,
        run=run,
        tree=tree,
        wait_seconds=wait_seconds,
        table=table,
        interrupts=1 if isinstance(pending, KeyboardInterrupt) else 0,
    )
    try:
        stopper.stop()
    except KeyboardInterrupt as exc:
        stopper.finish(first=exc)
        if isinstance(pending, KeyboardInterrupt):
            return
        raise
    except _StuckError:
        stopper.give_up(cause=pending)


@dataclass
class _Stopper:
    """One stop of one child: what :func:`stop_process` repeats."""

    process: subprocess.Popen[Any]
    windows: bool
    run: Runner
    tree: bool
    wait_seconds: float | None
    table: ProcessTable | None
    interrupts: int
    killed_at: float | None = None

    def kill(self) -> None:
        """Kill the child, and its tree as asked; note when, the first time."""
        if self.windows:
            # The handle Popen holds keeps this id from being reused.
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                self.run(  # nosec B603 - fixed absolute path, argv list
                    [_taskkill(), "/F", "/T", "/PID", str(self.process.pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=_TASKKILL_SECONDS,
                    check=False,
                )
        elif self.tree:
            kill_tree(self.process, table=self.table)
        with contextlib.suppress(OSError):
            self.process.kill()
        if self.killed_at is None:
            self.killed_at = time.monotonic()

    def stop(self) -> None:
        """Kill the child unless it has exited, then wait for it.

        Raises:
            _StuckError: lintro was interrupted and the child had not
                exited :data:`_STUCK_SECONDS` after its ``SIGKILL``.
        """
        if self.process.poll() is not None:
            return  # Exited, and poll() has reaped it.
        self.kill()
        if self.wait_seconds is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=self.wait_seconds)
            return
        if not self.interrupts:
            self.process.wait()
            return
        killed_at = time.monotonic() if self.killed_at is None else self.killed_at
        left = killed_at + _STUCK_SECONDS - time.monotonic()
        try:
            self.process.wait(timeout=max(left, 0.0))
        except subprocess.TimeoutExpired:
            raise _StuckError from None

    def finish(self, *, first: KeyboardInterrupt) -> None:
        """Repeat :meth:`stop` after the Ctrl-C *first*, until it is done.

        Args:
            first: The Ctrl-C that cut the stop short.
        """
        while True:
            self.interrupts += 1
            if self.interrupts >= _ESCAPE_INTERRUPTS:
                self.give_up(cause=first)
            try:
                self.stop()
                return
            except KeyboardInterrupt:
                continue
            except _StuckError:
                self.give_up(cause=first)

    def give_up(self, *, cause: BaseException | None) -> None:
        """Stop waiting for the child, unless it has exited after all.

        Args:
            cause: What the escape is raised from.

        Raises:
            ChildStillRunningError: It is still running; it was sent
                ``SIGKILL``.
        """
        if self.process.poll() is not None:
            return
        with contextlib.suppress(KeyboardInterrupt):
            self.kill()
        if self.process.poll() is not None:
            return
        logger.debug(
            f"process {self.process.pid} had not exited after SIGKILL when "
            "lintro was interrupted; lintro stopped waiting for it and leaves "
            "what it may still write, and its install lock, until lintro exits",
        )
        raise ChildStillRunningError(self.process.pid) from cause


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
    # Not ``with Popen``: its exit waits without limit for a child that a
    # ChildStillRunningError leaves running. Every other path has reaped it.
    process = subprocess.Popen(  # nosec B603 - args list, shell=False
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
    )
    try:
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
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
    return subprocess.CompletedProcess(
        list(argv),
        process.returncode,
        stdout,
        stderr,
    )
