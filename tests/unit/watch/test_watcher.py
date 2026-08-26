"""Tests for the watchdog event handler and watch lifecycle.

Filesystem events are synthetic and the observer is a mock, so the watcher
is exercised without touching a real filesystem or relying on timing.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.utils.tool_utils import VENV_PATTERNS
from lintro.watch.watcher import (
    LintroEventHandler,
    _build_ignore_spec,
    default_ignore_patterns,
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
    patterns = default_ignore_patterns()
    if ignore_patterns:
        patterns.extend(ignore_patterns)
    spec = _build_ignore_spec(patterns)
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


def test_event_callback_receives_change_kinds(fake_fs_event: EventBuilder) -> None:
    """Accepted filesystem events should retain their user-facing kind."""
    seen: list[tuple[str, str]] = []
    handler = LintroEventHandler(
        on_change=lambda _path: None,
        ignore_spec=_build_ignore_spec([]),
        on_event=lambda path, kind: seen.append((path, kind)),
    )

    handler.on_created(fake_fs_event("/proj/src/new.py"))
    handler.on_modified(fake_fs_event("/proj/src/new.py"))
    handler.on_moved(
        fake_fs_event("/proj/src/new.py", dest_path="/proj/src/moved.py"),
    )

    assert_that(seen).is_equal_to(
        [
            ("/proj/src/new.py", "created"),
            ("/proj/src/new.py", "modified"),
            ("/proj/src/moved.py", "moved"),
        ],
    )


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


@pytest.mark.parametrize(
    "directory",
    VENV_PATTERNS,
)
def test_environment_directories_are_ignored(
    tmp_path: Path,
    directory: str,
) -> None:
    """All standard environment directories are ignored by default.

    Args:
        tmp_path: Temporary watched project root.
        directory: Environment directory name under test.
    """
    environment_file = tmp_path / directory / "lib" / "module.py"
    environment_file.parent.mkdir(parents=True)
    environment_file.write_text("x = 1\n")

    batches = _drive_events(
        [str(tmp_path)],
        [str(environment_file)],
    )

    assert_that(batches).is_empty()


def test_custom_ignore_patterns_apply(fake_fs_event: EventBuilder) -> None:
    """Custom ignore patterns extend the built-in defaults."""
    handler, seen = _handler(ignore_patterns=["**/generated/**"])

    handler.on_modified(fake_fs_event("/proj/generated/api.py"))
    handler.on_modified(fake_fs_event("/proj/.git/index"))
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


def test_watch_paths_cleans_up_when_observer_start_fails(tmp_path: Path) -> None:
    """Observer cleanup cannot mask the original start exception."""
    observer = _MockObserver()

    def _fail_start() -> None:
        msg = "original startup failure"
        raise RuntimeError(msg)

    def _fail_join(*_args: Any, **_kwargs: Any) -> None:
        msg = "cannot join thread before it is started"
        raise RuntimeError(msg)

    observer.start = _fail_start  # type: ignore[method-assign]
    observer.join = _fail_join  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="original startup failure"):
        watch_paths(
            [str(tmp_path)],
            on_batch=lambda _batch: None,
            observer_factory=lambda: observer,
        )

    assert_that(observer.stopped).is_true()
    assert_that(observer.joined).is_false()


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


def _drive_events(
    watch_targets: list[str],
    event_paths: list[str],
    *,
    ignore_patterns: list[str] | None = None,
    include_venv: bool = False,
) -> list[set[str]]:
    """Run watch_paths and feed synthetic modify events to the handler.

    Args:
        watch_targets: Paths passed to watch_paths.
        event_paths: File paths to emit as modification events after start.
        ignore_patterns: Optional extra ignore patterns.
        include_venv: Whether virtual environment paths should produce events.

    Returns:
        The list of emitted batches.
    """
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
        for event_path in event_paths:
            handler_ref["handler"].on_modified(
                type("E", (), {"is_directory": False, "src_path": event_path})(),
            )
        stop_event.set()

    observer.start = _fake_start  # type: ignore[method-assign]

    watch_paths(
        watch_targets,
        on_batch=batches.append,
        debounce_ms=50_000,  # long, so only the shutdown flush emits
        ignore_patterns=ignore_patterns,
        include_venv=include_venv,
        stop_event=stop_event,
        observer_factory=lambda: observer,
    )
    return batches


def test_single_file_target_ignores_siblings(tmp_path: Path) -> None:
    """Watching one file must not react to sibling files in its directory."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    sibling = tmp_path / "bar.py"
    sibling.write_text("y = 2\n")

    batches = _drive_events(
        [str(target)],
        [str(sibling), str(target)],
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(target)})


def test_custom_ignore_patterns_extend_defaults(tmp_path: Path) -> None:
    """A custom ignore pattern must not re-enable the built-in defaults."""
    normal = tmp_path / "real.py"
    normal.write_text("x = 1\n")
    generated = tmp_path / "generated" / "api.py"
    generated.parent.mkdir()
    generated.write_text("gen = 1\n")
    git_index = tmp_path / ".git" / "index"
    git_index.parent.mkdir()
    git_index.write_text("ref\n")

    batches = _drive_events(
        [str(tmp_path)],
        [str(generated), str(git_index), str(normal)],
        ignore_patterns=["**/generated/**"],
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(normal)})


def test_custom_negation_cannot_reenable_mandatory_ignore(tmp_path: Path) -> None:
    """Custom negations must not re-enable built-in ignored directories."""
    normal = tmp_path / "real.py"
    normal.write_text("x = 1\n")
    git_index = tmp_path / ".git" / "index"
    git_index.parent.mkdir()
    git_index.write_text("ref\n")

    batches = _drive_events(
        [str(tmp_path)],
        [str(git_index), str(normal)],
        ignore_patterns=["!**/.git/**"],
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(normal)})


def test_lintro_run_output_is_ignored(tmp_path: Path) -> None:
    """A watch run must not retrigger itself from generated report files."""
    report = tmp_path / ".lintro" / "run-1" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Report\n", encoding="utf-8")
    normal = tmp_path / "real.py"
    normal.write_text("x = 1\n")

    batches = _drive_events(
        [str(tmp_path)],
        [str(report), str(normal)],
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(normal)})


def test_ignore_patterns_are_relative_to_watch_root(tmp_path: Path) -> None:
    """An ignored ancestor above the watch root does not drop project files."""
    project = tmp_path / "build" / "project"
    source = project / "src" / "app.py"
    generated = project / "build" / "generated.py"
    source.parent.mkdir(parents=True)
    generated.parent.mkdir()
    source.write_text("x = 1\n")
    generated.write_text("x = 2\n")

    batches = _drive_events(
        [str(project)],
        [str(source), str(generated)],
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(source)})


@pytest.mark.parametrize(
    "directory",
    [name for name in VENV_PATTERNS if name != "node_modules"],
)
def test_include_venv_forwards_environment_file_events(
    tmp_path: Path,
    directory: str,
) -> None:
    """``include_venv`` forwards every virtual environment directory.

    Args:
        tmp_path: Temporary watched project root.
        directory: Virtual environment directory name under test.
    """
    venv_file = tmp_path / directory / "lib" / "site.py"
    venv_file.parent.mkdir(parents=True)
    venv_file.write_text("x = 1\n")

    batches = _drive_events(
        [str(tmp_path)],
        [str(venv_file)],
        include_venv=True,
    )

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({str(venv_file)})
