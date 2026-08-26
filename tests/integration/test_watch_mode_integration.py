"""Integration coverage for watch mode with a real watchdog observer."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from assertpy import assert_that
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from lintro.watch.watcher import watch_paths


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


def test_real_observer_reports_create_modify_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    """A native observer should debounce real create/modify events and stop."""
    ready = threading.Event()
    stop = threading.Event()
    batch_ready = threading.Event()
    batches: list[set[str]] = []
    event_kinds: list[str] = []
    errors: list[BaseException] = []

    def _on_batch(batch: set[str]) -> None:
        batches.append(batch)
        batch_ready.set()

    def _run_watch() -> None:
        try:
            watch_paths(
                [str(tmp_path)],
                on_batch=_on_batch,
                on_event=lambda _path, kind: event_kinds.append(kind),
                debounce_ms=25,
                stop_event=stop,
                observer_factory=lambda: _SignalingObserver(ready),
            )
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            errors.append(exc)

    worker = threading.Thread(target=_run_watch)
    worker.start()
    try:
        assert_that(ready.wait(timeout=5)).is_true()
        target = tmp_path / "watched.py"
        target.write_text("value = 1\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=5)).is_true()

        batch_ready.clear()
        target.write_text("value = 2\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=5)).is_true()
    finally:
        stop.set()
        worker.join(timeout=5)

    assert_that(worker.is_alive()).is_false()
    assert_that(errors).is_empty()
    assert_that(set().union(*batches)).contains(str(target))
    assert_that(event_kinds).contains("created", "modified")


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
    batch_ready = threading.Event()
    batches: list[set[str]] = []

    def _on_batch(batch: set[str]) -> None:
        batches.append(batch)
        batch_ready.set()

    worker = threading.Thread(
        target=lambda: watch_paths(
            [str(tmp_path)],
            on_batch=_on_batch,
            debounce_ms=25,
            stop_event=stop,
            observer_factory=lambda: _SignalingObserver(ready),
        ),
    )
    worker.start()
    try:
        assert_that(ready.wait(timeout=5)).is_true()
        source.rename(destination)
        assert_that(batch_ready.wait(timeout=5)).is_true()
    finally:
        stop.set()
        worker.join(timeout=5)

    assert_that(worker.is_alive()).is_false()
    assert_that(set().union(*batches)).contains(str(expected))


def test_real_observer_ignores_lintro_run_output(tmp_path: Path) -> None:
    """Generated ``.lintro/run-*`` reports must not retrigger watch mode."""
    ready = threading.Event()
    stop = threading.Event()
    batch_ready = threading.Event()
    batches: list[set[str]] = []
    report = tmp_path / ".lintro" / "run-1" / "report.md"
    target = tmp_path / "real.py"

    def _on_batch(batch: set[str]) -> None:
        batches.append(batch)
        batch_ready.set()

    worker = threading.Thread(
        target=lambda: watch_paths(
            [str(tmp_path)],
            on_batch=_on_batch,
            debounce_ms=25,
            stop_event=stop,
            observer_factory=lambda: _SignalingObserver(ready),
        ),
    )
    worker.start()
    try:
        assert_that(ready.wait(timeout=5)).is_true()
        report.parent.mkdir(parents=True)
        report.write_text("# Report\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=0.25)).is_false()

        target.write_text("value = 1\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=5)).is_true()
    finally:
        stop.set()
        worker.join(timeout=5)

    assert_that(worker.is_alive()).is_false()
    assert_that(set().union(*batches)).contains(str(target))
    assert_that(set().union(*batches)).does_not_contain(str(report))
