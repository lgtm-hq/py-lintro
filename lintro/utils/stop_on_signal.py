"""Stop every live npx run when lintro is told to stop (#3530).

An npx run with a cache leads its own process group, so that a timeout can
kill npx and the install scripts it started together
(:mod:`lintro.utils.process_stop`). The terminal's Ctrl-C (``SIGINT``) and
hang-up (``SIGHUP``) go to the terminal's foreground group, and a ``kill``
to lintro alone, so none of them reaches npx any more. Python delivers a
signal to the main thread only, and npx often runs in a worker thread (a
parallel run, the default with more than one tool), so lintro has to pass
the request on itself.

Every npx run registers a :class:`StoppableRun` for its whole life, from
before it waits for the install lock until it has cleaned up and let go of
that lock (:class:`Stoppable`). While lintro runs tools, a handler for
``SIGINT``, ``SIGTERM`` and ``SIGHUP`` is installed on the main thread
(:func:`signal_guard`). It never signals or reaps a process itself: it
marks every registered run as stop-requested and then lets the previous
handler act. The thread that owns each run notices within
:data:`POLL_SECONDS`, kills npx's group while npx is still unreaped (only
that thread reaps it, so a group that already exited is never signalled),
waits for it, cleans up and releases the lock, and raises :class:`Stopped`.

- ``SIGINT`` and any signal whose previous handler is a Python callable:
  the previous handler runs next, as without lintro's handler. For Ctrl-C
  that is ``KeyboardInterrupt`` on the main thread, or asyncio's
  cancellation when the tools run on its event loop, so lintro's own exit
  code and messages are unchanged.
- ``SIGTERM``/``SIGHUP`` with the default action: when the main thread runs
  npx it unwinds first (through cleanup); otherwise the handler waits up to
  :data:`DRAIN_SECONDS` for the worker runs to finish stopping. Then the
  default action is restored and the signal re-raised, so lintro still dies
  of that signal.
- A signal whose handler was ``SIG_IGN`` (``nohup``) is left alone.

Once the main thread has begun stopping its own npx run (a first Ctrl-C, a
timeout, or cleanup after a failed install), a repeated signal is held
until the run has stopped npx, reaped it, cleaned up and released the lock,
and then re-raised; otherwise a second Ctrl-C could leave the lock released
beneath a live npx. Worker threads never receive ``KeyboardInterrupt``.

Handlers are installed only from the main thread and only while a run is
in progress: around an npx run on the main thread, and around the worker
threads of lintro's parallel executor. Whatever was installed before is
chained and restored, so a library caller (MCP server, API, watch mode,
pytest) keeps its own. From a thread that is not the main thread nothing is
installed. A caller that runs lintro in threads of its own, with no lintro
code on the main thread, is covered for Ctrl-C by a last resort: when the
main thread ends with ``KeyboardInterrupt``, a hook that runs before the
interpreter waits for its threads asks every live run to stop.

``SIGKILL`` cannot be handled. If lintro is killed that way, npx and what
it started run on in their own group until they finish, and the kernel
drops lintro's install lock; the next run may then meet the unfinished
entry and refuse it as untrusted. Linux's ``PR_SET_PDEATHSIG`` would reach
npx only (not its children), fires when the spawning thread exits rather
than the process, and needs a ``preexec_fn``, which is unsafe with threads;
macOS has no equivalent. Neither is used.
"""

from __future__ import annotations

import contextlib
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

#: Seconds between checks for a stop request while npx runs.
POLL_SECONDS: float = 0.05

#: Longest wait, in a ``SIGTERM``/``SIGHUP`` handler, for worker runs to stop.
DRAIN_SECONDS: float = 30.0

#: The signals that tell lintro to stop.
_SIGNAL_NAMES: tuple[str, ...] = ("SIGINT", "SIGTERM", "SIGHUP")

Handler = Callable[[int, FrameType | None], Any] | int | None


class Stopped(KeyboardInterrupt):
    """Raised in the thread running npx once a signal asked lintro to stop.

    ``signum`` is the signal that asked.
    """

    def __init__(self, signum: int) -> None:
        """Record *signum*.

        Args:
            signum: The signal that asked lintro to stop.
        """
        super().__init__(signum)
        self.signum = signum


class _Terminated(BaseException):
    """Unwinds the main thread for a signal whose default action ends lintro.

    ``signum`` is the signal to re-raise once npx is stopped and cleaned up.
    """

    def __init__(self, signum: int) -> None:
        """Record *signum*.

        Args:
            signum: The signal.
        """
        super().__init__(signum)
        self.signum = signum


@dataclass(eq=False)
class StoppableRun:
    """One npx run, from before its lock wait until its lock is released.

    Attributes:
        thread: Identifier of the thread that runs it.
        requested: Set once a signal asked lintro to stop.
        signum: The signal that asked, once one did.
        stopping: On the main thread, set once the run began stopping npx or
            cleaning up; later signals wait in *pending* until it is done.
        pending: Signals held while *stopping*, re-raised afterwards.
    """

    thread: int
    requested: threading.Event = field(default_factory=threading.Event)
    signum: int | None = None
    stopping: bool = False
    pending: list[int] = field(default_factory=list)

    def request(self, signum: int) -> None:
        """Ask this run to stop.

        Args:
            signum: The signal that asked.
        """
        if self.signum is None:
            self.signum = signum
        self.requested.set()

    def raise_if_requested(self) -> None:
        """Raise :class:`Stopped` if a signal asked lintro to stop.

        Raises:
            Stopped: A stop was requested.
        """
        if self.requested.is_set():
            raise Stopped(self.signum or signal.SIGINT)


#: Every live run; changed under :data:`_LOCK`, read by the handler as a copy.
_RUNS: set[StoppableRun] = set()
_LOCK = threading.Lock()
_LOCAL = threading.local()

# Main-thread state, changed only by the main thread (handler included).
_depth = 0
_previous: dict[int, Handler] = {}
_signalled: int | None = None
_exit_hook_registered = False


def _on_main_thread() -> bool:
    """Report whether this is the main thread, the only one signals reach.

    Returns:
        True on the main thread.
    """
    return threading.current_thread() is threading.main_thread()


def current_run() -> StoppableRun | None:
    """Return the npx run this thread is in, if any.

    Returns:
        The innermost registered run of this thread, or None.
    """
    run: StoppableRun | None = getattr(_LOCAL, "run", None)
    return run


def defer_signals() -> None:
    """Hold repeated signals until this thread's run is over.

    Called when the run begins stopping npx or cleaning up after it, so a
    second Ctrl-C cannot cut that short and release the lock beneath a live
    npx. Only the main thread receives signals; elsewhere this changes
    nothing that matters.
    """
    run = current_run()
    if run is not None:
        run.stopping = True


def _main_run() -> StoppableRun | None:
    """Return the run the main thread is in, if any.

    Returns:
        The main thread's registered run, or None.
    """
    main = threading.main_thread().ident
    for run in list(_RUNS):
        if run.thread == main:
            return run
    return None


def _drain(budget: float = DRAIN_SECONDS) -> None:
    """Wait until every registered run has stopped, or *budget* has passed.

    Args:
        budget: Longest wait in seconds.
    """
    give_up = time.monotonic() + budget
    while _RUNS and time.monotonic() < give_up:
        time.sleep(POLL_SECONDS)


def _on_signal(signum: int, frame: FrameType | None) -> None:
    """Ask every npx run to stop, then act as the previous handler would.

    Args:
        signum: The signal.
        frame: The interrupted frame, passed on to a chained handler.

    Raises:
        _Terminated: The default action would end lintro while the main
            thread runs npx; it unwinds through the cleanup first.
    """
    global _signalled
    for run in list(_RUNS):
        run.request(signum)
    main_run = _main_run()
    if main_run is not None and main_run.stopping:
        if signum not in main_run.pending:
            main_run.pending.append(signum)
        return
    _signalled = signum
    if main_run is not None:
        main_run.stopping = True
    previous = _previous.get(signum, signal.SIG_DFL)
    if callable(previous):
        previous(signum, frame)
        return
    if previous == signal.SIG_IGN:
        return
    if main_run is not None:
        raise _Terminated(signum)
    _drain()
    signal.signal(signum, previous)
    signal.raise_signal(signum)


def _on_interpreter_exit() -> None:
    """Stop live npx runs when the main thread ended with Ctrl-C.

    Runs before the interpreter joins its threads (and before
    ``concurrent.futures`` waits for its workers), so a worker running npx
    does not keep lintro alive until npx finishes. After a normal end of
    the main thread nothing is stopped.
    """
    if isinstance(getattr(sys, "last_value", None), KeyboardInterrupt):
        for run in list(_RUNS):
            run.request(signal.SIGINT)


def _register_exit_hook() -> None:
    """Register :func:`_on_interpreter_exit` once, if this Python allows it."""
    global _exit_hook_registered
    if _exit_hook_registered:
        return
    _exit_hook_registered = True
    register = getattr(threading, "_register_atexit", None)
    if register is not None:
        with contextlib.suppress(RuntimeError):  # Shutdown has begun.
            register(_on_interpreter_exit)


def _signals() -> Iterator[int]:
    """Yield the stop signals this platform has.

    Yields:
        int: Each signal number.
    """
    for name in _SIGNAL_NAMES:
        signum = getattr(signal, name, None)
        if signum is not None:
            yield int(signum)


def _install() -> None:
    """Install :func:`_on_signal`, remembering what each signal had."""
    for signum in _signals():
        current = signal.getsignal(signum)
        if current is None or current == signal.SIG_IGN:
            continue  # Not set from Python, or ignored on purpose (nohup).
        with contextlib.suppress(OSError, ValueError):
            signal.signal(signum, _on_signal)
            _previous[signum] = current


def _restore() -> None:
    """Put back each handler :func:`_install` replaced, if still replaced."""
    global _signalled
    for signum, previous in list(_previous.items()):
        if signal.getsignal(signum) is _on_signal:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signum, previous)
    _previous.clear()
    _signalled = None


@contextlib.contextmanager
def signal_guard() -> Iterator[None]:
    """Have a stop signal stop every npx run while the block runs.

    Nests; the outermost guard installs and restores. Off the main thread
    nothing is installed.

    Yields:
        None: The block runs with the handlers installed.
    """
    global _depth
    if not _on_main_thread():
        yield
        return
    if _depth == 0:
        _install()
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0:
            _restore()


class Stoppable:
    """Register an npx run for its whole life, so a signal can stop it.

    On the main thread the run also holds a :func:`signal_guard`. When the
    block ends, signals held back while it was stopping are re-raised, after
    the lock it guarded is released.
    """

    def __init__(self) -> None:
        """Create the run for this thread."""
        self.run = StoppableRun(thread=threading.get_ident())
        self._guard: contextlib.AbstractContextManager[None] | None = None
        self._outer: StoppableRun | None = None

    def __enter__(self) -> StoppableRun:
        """Register the run.

        Returns:
            The run.
        """
        if not _on_main_thread():
            with _LOCK:
                _register_exit_hook()
        self._guard = signal_guard()
        self._guard.__enter__()
        self._outer = current_run()
        _LOCAL.run = self.run
        with _LOCK:
            _RUNS.add(self.run)
        if _signalled is not None:
            self.run.request(_signalled)
        return self.run

    def __exit__(self, kind: type[BaseException] | None, exc: Any, tb: Any) -> None:
        """Unregister the run, then re-raise any signal held back.

        Args:
            kind: The exception type, if the block raised.
            exc: The exception, if the block raised.
            tb: Its traceback.

        Raises:
            SystemExit: A held ``SIGTERM``/``SIGHUP`` did not end lintro when
                re-raised (its handler changed meanwhile).
        """
        with _LOCK:
            _RUNS.discard(self.run)
        _LOCAL.run = self._outer
        pending = list(self.run.pending)
        if isinstance(exc, _Terminated) and exc.signum not in pending:
            pending.append(exc.signum)
        guard, self._guard = self._guard, None
        if guard is not None:
            guard.__exit__(None, None, None)
        for signum in pending:
            signal.raise_signal(signum)
        if isinstance(exc, _Terminated):
            raise SystemExit(128 + exc.signum) from exc
