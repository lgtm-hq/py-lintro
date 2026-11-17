"""Integration coverage for watch mode with a real watchdog observer."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from lintro.watch.runner import WatchRunner
from lintro.watch.watcher import watch_paths

# Upper bound for each wait. Waits return as soon as their condition holds,
# so this only matters when the watcher is broken or the machine is starved.
_DEADLINE_S = 10.0


class _SignalingObserver:
    """Observer that exposes when its native emitter threads are ready."""

    def __init__(self, ready: threading.Event) -> None:
        """Initialize the observer.

        Args:
            ready: Event set after the observer starts successfully.
        """
        self._observer = Observer()
        self._ready = ready

    def schedule(
        self,
        event_handler: FileSystemEventHandler,
        path: str,
        *,
        recursive: bool = False,
    ) -> Any:
        """Delegate watch registration to the native observer.

        Args:
            event_handler: Handler receiving native filesystem events.
            path: Directory to observe.
            recursive: Whether to observe subdirectories.

        Returns:
            Native watchdog watch handle.
        """
        return self._observer.schedule(
            event_handler,
            path,
            recursive=recursive,
        )

    def start(self) -> None:
        """Start native observation and signal readiness."""
        self._observer.start()
        self._ready.set()

    def stop(self) -> None:
        """Stop native observation."""
        self._observer.stop()

    def join(self, timeout: float | None = None) -> None:
        """Wait for native observation to stop.

        Args:
            timeout: Optional maximum wait in seconds.
        """
        self._observer.join(timeout=timeout)


class _WatchRecorder:
    """Thread-safe record of watcher output that tests can wait on.

    Native backends do not deliver one batch per write. FSEvents (macOS)
    replays events from shortly before the stream started, splits one write
    into several native events, and delivers trailing metadata events for a
    file after its first batch has fired. A test that waits for "the next
    batch" can therefore wake on an unrelated or stale batch, so every wait
    here names the path it expects and returns as soon as that path shows up.
    """

    def __init__(self) -> None:
        """Initialize empty batch and event logs."""
        self._changed = threading.Condition()
        self.batches: list[set[str]] = []
        self.events: list[tuple[str, str]] = []

    def on_batch(self, batch: set[str]) -> None:
        """Record a debounced batch and wake waiters.

        Args:
            batch: Paths delivered by the debouncer.
        """
        with self._changed:
            self.batches.append(set(batch))
            self._changed.notify_all()

    def on_event(self, path: str, kind: str) -> None:
        """Record an accepted filesystem event and wake waiters.

        Args:
            path: Path reported by the watcher.
            kind: Event kind reported by the watcher.
        """
        with self._changed:
            self.events.append((path, kind))
            self._changed.notify_all()

    def mark(self) -> tuple[int, int]:
        """Return the current batch and event counts.

        Returns:
            Positions to pass as ``since`` to the wait helpers.
        """
        with self._changed:
            return len(self.batches), len(self.events)

    def wait_for_batch_with(
        self,
        path: Path,
        *,
        since: tuple[int, int] = (0, 0),
    ) -> bool:
        """Wait until a batch recorded after ``since`` contains ``path``.

        Args:
            path: Path expected in a batch.
            since: Mark taken before the write that should produce it.

        Returns:
            True when such a batch arrived before the deadline.
        """
        with self._changed:
            return self._changed.wait_for(
                lambda: any(str(path) in batch for batch in self.batches[since[0] :]),
                timeout=_DEADLINE_S,
            )

    def wait_for_event(
        self,
        path: Path,
        kind: str,
        *,
        since: tuple[int, int] = (0, 0),
    ) -> bool:
        """Wait until an event of ``kind`` for ``path`` is recorded after ``since``.

        Args:
            path: Path expected in the event.
            kind: Event kind expected.
            since: Mark taken before the write that should produce it.

        Returns:
            True when such an event arrived before the deadline.
        """
        with self._changed:
            return self._changed.wait_for(
                lambda: (str(path), kind) in self.events[since[1] :],
                timeout=_DEADLINE_S,
            )

    def kinds_for(self, path: Path) -> list[str]:
        """Return every recorded event kind for ``path``.

        Args:
            path: Path to look up.

        Returns:
            Event kinds in arrival order.
        """
        with self._changed:
            return [kind for event_path, kind in self.events if event_path == str(path)]

    def all_paths(self) -> set[str]:
        """Return the union of every recorded batch.

        Returns:
            All paths delivered so far.
        """
        with self._changed:
            return set().union(*self.batches)


def _start_watch(
    root: Path,
    *,
    recorder: _WatchRecorder,
    ready: threading.Event,
    stop: threading.Event,
) -> tuple[threading.Thread, list[BaseException]]:
    """Start a real watcher while retaining worker exceptions for assertions.

    Args:
        root: Directory observed recursively.
        recorder: Sink for debounced batches and accepted events.
        ready: Event set after native observer startup.
        stop: Event requesting watcher shutdown.

    Returns:
        Worker thread and mutable list of uncaught worker exceptions.
    """
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            watch_paths(
                [str(root)],
                on_batch=recorder.on_batch,
                on_event=recorder.on_event,
                debounce_ms=25,
                stop_event=stop,
                observer_factory=lambda: _SignalingObserver(ready),
            )
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    worker = threading.Thread(target=_run)
    worker.start()
    return worker, errors


def _handshake_watcher(
    root: Path,
    *,
    ready: threading.Event,
    recorder: _WatchRecorder,
) -> None:
    """Prove native watches are installed with an observed sentinel write.

    Waits for the sentinel itself rather than for the first batch: FSEvents
    may first replay files written just before the stream started.

    Args:
        root: Watched directory.
        ready: Native observer startup event.
        recorder: Sink for debounced batches and accepted events.
    """
    assert_that(ready.wait(timeout=_DEADLINE_S)).is_true()
    sentinel = root / ".watch-ready"
    sentinel.write_text("ready\n", encoding="utf-8")
    assert_that(recorder.wait_for_batch_with(sentinel)).is_true()


def _stop_watch(stop: threading.Event, worker: threading.Thread) -> None:
    """Request shutdown and wait a bounded time for the worker to exit.

    Args:
        stop: Event requesting watcher shutdown.
        worker: Thread running :func:`watch_paths`.
    """
    stop.set()
    worker.join(timeout=_DEADLINE_S)


def test_real_observer_reports_create_modify_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    """A native observer should debounce real create/modify events and stop."""
    ready = threading.Event()
    stop = threading.Event()
    recorder = _WatchRecorder()
    target = tmp_path / "watched.py"

    worker, errors = _start_watch(tmp_path, recorder=recorder, ready=ready, stop=stop)
    try:
        _handshake_watcher(tmp_path, ready=ready, recorder=recorder)

        before_create = recorder.mark()
        target.write_text("value = 1\n", encoding="utf-8")
        assert_that(
            recorder.wait_for_batch_with(target, since=before_create),
        ).is_true()

        before_modify = recorder.mark()
        target.write_text("value = 2\n", encoding="utf-8")
        assert_that(
            recorder.wait_for_event(target, "modified", since=before_modify),
        ).is_true()
        assert_that(
            recorder.wait_for_batch_with(target, since=before_modify),
        ).is_true()
    finally:
        _stop_watch(stop, worker)

    assert_that(worker.is_alive()).is_false()
    assert_that(errors).is_empty()
    kinds = recorder.kinds_for(target)
    assert_that(set(kinds)).is_subset_of({"created", "modified"})
    if sys.platform != "darwin":
        # inotify and ReadDirectoryChangesW report the creation as its own
        # event. FSEvents does not guarantee it: macOS can deliver the new
        # file's provenance-xattr event before the event flagged "created",
        # and watchdog then treats that inode as already seen and drops the
        # created flag, so the new file is reported only as "modified".
        assert_that(kinds).contains("created")


def test_real_observer_expands_directory_move_events(tmp_path: Path) -> None:
    """Recursive watchdog backends should report files inside moved directories."""
    source = tmp_path / "old"
    source.mkdir()
    nested = source / "nested.py"
    nested.write_text("value = 1\n", encoding="utf-8")
    destination = tmp_path / "new"
    expected = destination / "nested.py"
    ready = threading.Event()
    stop = threading.Event()
    recorder = _WatchRecorder()

    worker, errors = _start_watch(tmp_path, recorder=recorder, ready=ready, stop=stop)
    try:
        _handshake_watcher(tmp_path, ready=ready, recorder=recorder)
        before_move = recorder.mark()
        source.rename(destination)
        assert_that(
            recorder.wait_for_batch_with(expected, since=before_move),
        ).is_true()
    finally:
        _stop_watch(stop, worker)

    assert_that(worker.is_alive()).is_false()
    assert_that(errors).is_empty()


def test_real_observer_ignores_lintro_run_output(tmp_path: Path) -> None:
    """Generated ``.lintro/run-*`` reports must not retrigger watch mode."""
    ready = threading.Event()
    stop = threading.Event()
    recorder = _WatchRecorder()
    report = tmp_path / ".lintro" / "run-1" / "report.md"
    target = tmp_path / "real.py"

    worker, errors = _start_watch(tmp_path, recorder=recorder, ready=ready, stop=stop)
    try:
        _handshake_watcher(tmp_path, ready=ready, recorder=recorder)
        report.parent.mkdir(parents=True)
        report.write_text("# Report\n", encoding="utf-8")
        # Native backends deliver events in write order and the debouncer
        # only merges, never reorders, so once the later write is reported
        # an unfiltered report event would already be in some batch.
        target.write_text("value = 1\n", encoding="utf-8")
        assert_that(recorder.wait_for_batch_with(target)).is_true()
    finally:
        _stop_watch(stop, worker)

    assert_that(worker.is_alive()).is_false()
    assert_that(errors).is_empty()
    assert_that(recorder.all_paths()).does_not_contain(str(report))
    assert_that(recorder.kinds_for(report)).is_empty()


def test_watch_runner_invokes_real_executor_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real watch batch should execute without backend keyword mismatches."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "valid.py"
    target.write_text("value = 1\n", encoding="utf-8")
    lines: list[str] = []
    runner = WatchRunner(
        restrict_to=["ruff"],
        watch_paths=[str(tmp_path)],
        emit=lines.append,
    )

    result = runner.run_batch({str(target)})

    assert_that(result).is_equal_to(0)
    assert_that(
        any(line.startswith("  ├─ ruff: ✅ passed") for line in lines),
    ).is_true()
    assert_that("\n".join(lines)).does_not_contain("TypeError")
