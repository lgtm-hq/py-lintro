"""Stop a child process and reap it before lintro moves on (#3530).

After an npx run that did not finish, lintro removes the half-written install
and lets go of its exclusive install lock. That is only safe once npx can no
longer write to the cache, so every way out of a run, a timeout, an
interruption (Ctrl-C) or any other exception, first kills the child and waits
for it to exit.

On Windows ``npx`` is ``npx.cmd``: killing the process lintro started ends
``cmd.exe`` only, and the ``node`` it started would go on installing. There
the whole tree is ended with ``taskkill /T`` first, while the process handle
lintro holds keeps its process id from being reused.
"""

from __future__ import annotations

import contextlib
import os
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
) -> None:
    """Kill *process*, and on Windows its children, then wait for it to exit.

    The wait has no time limit: a killed process cannot ignore the signal,
    and returning before it has exited would let lintro clean up beneath it.

    Args:
        process: The child, which may already have exited.
        windows: Whether this is Windows; defaults to ``os.name == "nt"``.
        run: What runs ``taskkill``.
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
    with contextlib.suppress(OSError):
        process.kill()
    process.wait()


def run_reaped(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | IO[Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *argv* to completion like ``subprocess.run`` with captured text.

    Unlike ``subprocess.run``, which waits only a quarter of a second for a
    child it killed on ``KeyboardInterrupt``, the child is always stopped
    and reaped (:func:`stop_process`) before any exception leaves.

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
    ) as process:
        finished = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            finished = True
        except subprocess.TimeoutExpired as exc:
            stop_process(process)
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
                stop_process(process)
    return subprocess.CompletedProcess(
        list(argv),
        process.returncode,
        stdout,
        stderr,
    )
