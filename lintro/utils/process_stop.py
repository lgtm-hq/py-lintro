"""Stop a child process and reap it before lintro moves on (#3530).

After an npx run that did not finish, lintro removes the half-written install
and lets go of its exclusive install lock. That is only safe once nothing npx
started can write to the cache, so every way out of a run, a timeout, an
interruption (Ctrl-C) or any other exception, first kills npx and what it
started and waits for npx to exit.

npm runs a package's install scripts as children of npx, so killing npx
alone would leave them writing into the entry. On POSIX an npx run is
therefore started as the leader of its own session (:func:`own_group`) and
the whole process group is killed. Its group id is npx's process id, and
that id cannot be reused while npx is unreaped, so the group is signalled
only while ``poll()`` says npx is still running. Ctrl-C at the terminal goes
to the terminal's foreground group, which no longer includes npx: lintro
gets the ``KeyboardInterrupt`` and ends the group itself. A child that moves
itself into another session or group escapes this.

On Windows ``npx`` is ``npx.cmd``: killing the process lintro started ends
``cmd.exe`` only, and the ``node`` it started would go on installing. There
the whole tree is ended with ``taskkill /T`` first, while the process handle
lintro holds keeps its process id from being reused. Windows has no process
groups to kill this way, and a process ``taskkill`` cannot see as part of
the tree (one whose parent already exited) is not ended.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - argv lists only, shell=False
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any

#: Seconds ``taskkill`` may take to end a process tree.
_TASKKILL_SECONDS: float = 10.0

#: Seconds spent collecting output a killed process left in its pipes.
_DRAIN_SECONDS: float = 1.0

#: Runs ``taskkill``; replaceable so the call can be observed off Windows.
Runner = Callable[..., object]


def own_group(*, windows: bool | None = None) -> bool:
    """Return ``Popen``'s ``start_new_session`` for a child in its own group.

    The child leads a new session, and so its own process group, on POSIX.
    Windows has no such option.

    Args:
        windows: Whether this is Windows; defaults to ``os.name == "nt"``.

    Returns:
        True on POSIX, False on Windows.
    """
    return not (os.name == "nt" if windows is None else windows)


def _taskkill() -> str:
    r"""Return ``taskkill``'s full path, so ``PATH`` cannot substitute it.

    Returns:
        ``%SystemRoot%\System32\taskkill.exe``.
    """
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return str(Path(root) / "System32" / "taskkill.exe")


def _wait_until_exited(process: subprocess.Popen[Any]) -> None:
    """Wait for *process* to exit, even through a repeated Ctrl-C.

    Args:
        process: The killed child.

    Raises:
        KeyboardInterrupt: Re-raised once the child has exited, when one
            arrived during the wait.
    """
    interrupted = False
    while True:
        try:
            process.wait()
        except KeyboardInterrupt:
            interrupted = True
            continue
        break
    if interrupted:
        raise KeyboardInterrupt


def stop_process(
    process: subprocess.Popen[Any],
    *,
    windows: bool | None = None,
    run: Runner = subprocess.run,
    group: bool = False,
    wait_seconds: float | None = None,
) -> None:
    """Kill *process* and what it started, then wait for it to exit.

    Args:
        process: The child, which may already have exited; then nothing is
            signalled, since its id may belong to another process by now.
        windows: Whether this is Windows; defaults to ``os.name == "nt"``.
        run: What runs ``taskkill``.
        group: Whether *process* was started with :func:`own_group`; then
            on POSIX its whole process group is killed.
        wait_seconds: How long to wait for it to exit, or None to wait until
            it has, through a repeated Ctrl-C too. Cleanup that must not run
            beneath a live process needs None; anything else may give up.
    """
    if process.poll() is not None:
        return  # Exited, and poll() has reaped it.
    if os.name == "nt" if windows is None else windows:
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
    elif group:
        # Unreaped, so its id, which is the group's, is still its own.
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()
    if wait_seconds is None:
        _wait_until_exited(process)
        return
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=wait_seconds)


def run_reaped(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | IO[Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *argv* to completion like ``subprocess.run`` with captured text.

    The child runs in its own process group (:func:`own_group`). Unlike
    ``subprocess.run``, which waits only a quarter of a second for a child
    it killed on ``KeyboardInterrupt`` and kills that child alone, the whole
    group is stopped and the child reaped (:func:`stop_process`) before any
    exception leaves.

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
    """
    with subprocess.Popen(  # nosec B603 - args list, shell=False
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=own_group(),
    ) as process:
        finished = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            finished = True
        except subprocess.TimeoutExpired as exc:
            stop_process(process, group=True)
            output: str | bytes | None
            errors: str | bytes | None
            try:
                output, errors = process.communicate(timeout=_DRAIN_SECONDS)
            except subprocess.TimeoutExpired as drained:
                # A grandchild still holds the pipes open.
                output, errors = drained.output, drained.stderr
            raise subprocess.TimeoutExpired(
                list(argv),
                timeout,
                output=output,
                stderr=errors,
            ) from exc
        finally:
            if not finished:
                stop_process(process, group=True)
    return subprocess.CompletedProcess(
        list(argv),
        process.returncode,
        stdout,
        stderr,
    )
