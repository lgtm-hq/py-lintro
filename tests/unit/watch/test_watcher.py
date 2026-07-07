"""Tests for the watchdog event handler and watch lifecycle.

Filesystem events are synthetic and the observer is a mock, so the watcher
is exercised without touching a real filesystem or relying on timing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from assertpy import assert_that

from lintro.watch.watcher import (
    DEFAULT_IGNORE_PATTERNS,
    LintroEventHandler,
    _build_ignore_spec,
    watch_paths,
)

EventBuilder = Callable[..., Any]


def _handler(
    ignore_patterns: list[str] | None = None,
) -> tuple[LintroEventHandler, list[str]]:
    """Build a handler recording forwarded paths.

    Args:
        ignore_patterns: Patterns to ignore; defaults to the built-ins.

    Returns:
        A tuple of the handler and the list it appends changed paths to.
    """
    seen: list[str] = []
    spec = _build_ignore_spec(ignore_patterns or DEFAULT_IGNORE_PATTERNS)
    handler = LintroEventHandler(on_change=seen.append, ignore_spec=spec)
    return handler, seen


def test_modified_file_is_forwarded(fake_fs_event: EventBuilder) -> None:
    """A file modification is forwarded to on_change."""
    handler, seen = _handler()

    handler.on_modified(fake_fs_event("/proj/src/foo.py"))

    assert_that(seen).is_equal_to(["/proj/src/foo.py"])


def test_created_file_is_forwarded(fake_fs_event: EventBuilder) -> None:
    """A file creation is forwarded to on_change."""
    handler, seen = _handler()

    handler.on_created(fake_fs_event("/proj/src/new.py"))

    assert_that(seen).is_equal_to(["/proj/src/new.py"])


def test_directory_events_are_ignored(fake_fs_event: EventBuilder) -> None:
    """Directory events never trigger a run."""
    handler, seen = _handler()

    handler.on_modified(fake_fs_event("/proj/src", is_directory=True))

    assert_that(seen).is_empty()


def test_moved_event_uses_destination(fake_fs_event: EventBuilder) -> None:
    """A move/rename forwards the destination path."""
    handler, seen = _handler()

    handler.on_moved(
        fake_fs_event("/proj/src/old.py", dest_path="/proj/src/renamed.py"),
    )

    assert_that(seen).is_equal_to(["/proj/src/renamed.py"])


def test_git_directory_is_ignored(fake_fs_event: EventBuilder) -> None:
    """Changes under .git are filtered out by default."""
    handler, seen = _handler()

    handler.on_modified(fake_fs_event("/proj/.git/index"))

    assert_that(seen).is_empty()


def test_pycache_is_ignored(fake_fs_event: EventBuilder) -> None:
    """Changes under __pycache__ are filtered out by default."""
    handler, seen = _handler()

    handler.on_modified(fake_fs_event("/proj/src/__pycache__/foo.cpython-311.pyc"))

    assert_that(seen).is_empty()


def test_pyc_files_are_ignored(fake_fs_event: EventBuilder) -> None:
    """Compiled .pyc files are ignored by default."""
    handler, seen = _handler()

    handler.on_modified(fake_fs_event("/proj/src/foo.pyc"))

    assert_that(seen).is_empty()


def test_custom_ignore_patterns_apply(fake_fs_event: EventBuilder) -> None:
    """Custom ignore patterns replace the defaults."""
    handler, seen = _handler(ignore_patterns=["**/generated/**"])

    handler.on_modified(fake_fs_event("/proj/generated/api.py"))
    handler.on_modified(fake_fs_event("/proj/src/real.py"))

    assert_that(seen).is_equal_to(["/proj/src/real.py"])


def test_bytes_path_is_decoded(fake_fs_event: EventBuilder) -> None:
    """A bytes event path is decoded to str before forwarding."""
    handler, seen = _handler()

    handler.on_modified(fake_fs_event(b"/proj/src/foo.py"))

    assert_that(seen).is_equal_to(["/proj/src/foo.py"])


class _MockObserver:
    """A minimal stand-in for a watchdog Observer.

    Records scheduled watches and lifecycle calls so the watch loop can be
    driven without a real filesystem.
    """

    def __init__(self) -> None:
        """Initialize the mock observer's recording state."""
        self.scheduled: list[tuple[str, bool]] = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(
        self,
        handler: Any,
        path: str,
        recursive: bool = False,
    ) -> None:
        """Record a scheduled watch.

        Args:
            handler: The event handler (unused).
            path: Path being watched.
            recursive: Whether the watch is recursive.
        """
        self.scheduled.append((path, recursive))

    def start(self) -> None:
        """Mark the observer as started."""
        self.started = True

    def stop(self) -> None:
        """Mark the observer as stopped."""
        self.stopped = True

    def join(self, *args: Any, **kwargs: Any) -> None:
        """Mark the observer as joined."""
        self.joined = True


def test_watch_paths_lifecycle_starts_and_stops(tmp_path: Path) -> None:
    """watch_paths starts the observer, watches the dir, then stops cleanly."""
    observer = _MockObserver()
    stop_event = threading.Event()
    stop_event.set()  # cause the loop to exit immediately

    watch_paths(
        [str(tmp_path)],
        on_batch=lambda _batch: None,
        debounce_ms=10,
        stop_event=stop_event,
        observer_factory=lambda: observer,
    )

    assert_that(observer.started).is_true()
    assert_that(observer.stopped).is_true()
    assert_that(observer.joined).is_true()
    assert_that(observer.scheduled).is_length(1)
    assert_that(observer.scheduled[0][0]).is_equal_to(str(tmp_path))
    assert_that(observer.scheduled[0][1]).is_true()


def test_watch_paths_watches_parent_dir_for_file_target(tmp_path: Path) -> None:
    """Watching a single file schedules a recursive watch on its parent."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    observer = _MockObserver()
    stop_event = threading.Event()
    stop_event.set()

    watch_paths(
        [str(target)],
        on_batch=lambda _batch: None,
        stop_event=stop_event,
        observer_factory=lambda: observer,
    )

    assert_that(observer.scheduled[0][0]).is_equal_to(str(tmp_path))


def test_watch_paths_flushes_pending_on_stop(tmp_path: Path) -> None:
    """A pending change is flushed when the loop stops."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    batches: list[set[str]] = []
    observer = _MockObserver()
    stop_event = threading.Event()

    handler_ref: dict[str, Any] = {}

    def _capture_schedule(handler: Any, path: str, recursive: bool = False) -> None:
        handler_ref["handler"] = handler
        observer.scheduled.append((path, recursive))

    observer.schedule = _capture_schedule  # type: ignore[method-assign]

    def _fake_start() -> None:
        observer.started = True
        # Simulate an event arriving after start, then request stop.
        handler_ref["handler"].on_modified(
            type("E", (), {"is_directory": False, "src_path": str(target)})(),
        )
        stop_event.set()

    observer.start = _fake_start  # type: ignore[method-assign]

    watch_paths(
        [str(tmp_path)],
        on_batch=batches.append,
        debounce_ms=50_000,  # long, so only the shutdown flush emits it
        stop_event=stop_event,
        observer_factory=lambda: observer,
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(target)})

