"""Integration coverage for watch mode with a real watchdog observer."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from lintro.watch.runner import WatchRunner
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


def _start_watch(
    root: Path,
    *,
    on_batch: Callable[[set[str]], None],
    ready: threading.Event,
    stop: threading.Event,
    on_event: Callable[[str, str], None] | None = None,
) -> tuple[threading.Thread, list[BaseException]]:
    """Start a real watcher while retaining worker exceptions for assertions.

    Args:
        root: Directory observed recursively.
        on_batch: Callback receiving debounced path batches.
        ready: Event set after native observer startup.
        stop: Event requesting watcher shutdown.
        on_event: Optional callback receiving path and event kind.

    Returns:
        Worker thread and mutable list of uncaught worker exceptions.
    """
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            watch_paths(
                [str(root)],
                on_batch=on_batch,
                on_event=on_event,
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
    batch_ready: threading.Event,
    batches: list[set[str]],
) -> None:
    """Prove native watches are installed with an observed sentinel write.

    Args:
        root: Watched directory.
        ready: Native observer startup event.
        batch_ready: Event set by the batch callback.
        batches: Recorded debounced batches.
    """
    assert_that(ready.wait(timeout=5)).is_true()
    sentinel = root / ".watch-ready"
    sentinel.write_text("ready\n", encoding="utf-8")
    assert_that(batch_ready.wait(timeout=5)).is_true()
    assert_that(set().union(*batches)).contains(str(sentinel))
    batches.clear()
    batch_ready.clear()


def test_real_observer_reports_create_modify_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    """A native observer should debounce real create/modify events and stop."""
    ready = threading.Event()
    stop = threading.Event()
    batch_ready = threading.Event()
    batches: list[set[str]] = []
    event_kinds: list[str] = []

    def _on_batch(batch: set[str]) -> None:
        batches.append(batch)
        batch_ready.set()

    worker, errors = _start_watch(
        tmp_path,
        on_batch=_on_batch,
        on_event=lambda _path, kind: event_kinds.append(kind),
        ready=ready,
        stop=stop,
    )
    try:
        _handshake_watcher(
            tmp_path,
            ready=ready,
            batch_ready=batch_ready,
            batches=batches,
        )
        event_kinds.clear()
        target = tmp_path / "watched.py"
        target.write_text("value = 1\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=5)).is_true()

        batch_ready.clear()
        batch_ready.wait(timeout=0.1)
        batch_ready.clear()
        first_write_batches = len(batches)
        target.write_text("value = 2\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=5)).is_true()
        assert_that(len(batches)).is_greater_than(first_write_batches)
        assert_that(batches[-1]).contains(str(target))
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

    worker, errors = _start_watch(
        tmp_path,
        on_batch=_on_batch,
        ready=ready,
        stop=stop,
    )
    try:
        _handshake_watcher(
            tmp_path,
            ready=ready,
            batch_ready=batch_ready,
            batches=batches,
        )
        source.rename(destination)
        assert_that(batch_ready.wait(timeout=5)).is_true()
    finally:
        stop.set()
        worker.join(timeout=5)

    assert_that(worker.is_alive()).is_false()
    assert_that(errors).is_empty()
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

    worker, errors = _start_watch(
        tmp_path,
        on_batch=_on_batch,
        ready=ready,
        stop=stop,
    )
    try:
        _handshake_watcher(
            tmp_path,
            ready=ready,
            batch_ready=batch_ready,
            batches=batches,
        )
        report.parent.mkdir(parents=True)
        report.write_text("# Report\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=0.25)).is_false()

        target.write_text("value = 1\n", encoding="utf-8")
        assert_that(batch_ready.wait(timeout=5)).is_true()
    finally:
        stop.set()
        worker.join(timeout=5)

    assert_that(worker.is_alive()).is_false()
    assert_that(errors).is_empty()
    assert_that(set().union(*batches)).contains(str(target))
    assert_that(set().union(*batches)).does_not_contain(str(report))


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
