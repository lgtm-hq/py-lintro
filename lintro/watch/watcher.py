"""Wire watchdog filesystem events into the debouncer and runner.

This module owns the parts that talk to :mod:`watchdog`:

* :class:`LintroEventHandler` translates raw filesystem events into
  debounced change notifications, filtering out ignored paths.
* :func:`watch_paths` sets up an observer over the requested paths, blocks
  until interrupted, and shuts everything down cleanly on Ctrl-C.

The filtering and event-translation logic lives on the handler so it can be
unit-tested with synthetic events, without a real filesystem or sleeps.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pathspec
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from lintro.watch.debouncer import Debouncer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from rich.console import Console

__all__ = ["DEFAULT_IGNORE_PATTERNS", "LintroEventHandler", "watch_paths"]

# Gitignore-style patterns excluded from watching by default. Keeps noisy or
# irrelevant directories (VCS internals, caches, build artifacts, virtualenvs)
# from triggering runs.
DEFAULT_IGNORE_PATTERNS: list[str] = [
    "**/.git/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.pytest_cache/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/dist/**",
    "**/build/**",
    "**/*.pyc",
]


def _build_ignore_spec(patterns: Iterable[str]) -> pathspec.GitIgnoreSpec:
    """Compile gitignore-style patterns into a matcher.

    Args:
        patterns: Iterable of gitignore-style patterns.

    Returns:
        A compiled :class:`pathspec.GitIgnoreSpec`.
    """
    return pathspec.GitIgnoreSpec.from_lines(list(patterns))


class LintroEventHandler(FileSystemEventHandler):
    """Translate watchdog events into debounced, filtered change signals.

    Directory events are ignored; only file create/modify/move events are
    forwarded, and only when the path is not matched by the ignore spec.
    """

    def __init__(
        self,
        *,
        on_change: Callable[[str], None],
        ignore_spec: pathspec.GitIgnoreSpec,
    ) -> None:
        """Initialize the handler.

        Args:
            on_change: Called with a path string for each relevant change.
            ignore_spec: Compiled matcher; matching paths are dropped.
        """
        super().__init__()
        self._on_change = on_change
        self._ignore_spec = ignore_spec

    def _handle(self, path: str) -> None:
        """Forward a path to ``on_change`` unless it is ignored.

        Args:
            path: Filesystem path from the event.
        """
        if self._is_ignored(path):
            return
        self._on_change(path)

    def _is_ignored(self, path: str) -> bool:
        """Return whether a path matches the ignore spec.

        Args:
            path: Filesystem path to test.

        Returns:
            True if the path should be ignored.
        """
        # Match against a normalized relative-ish posix form so patterns like
        # ``**/__pycache__/**`` behave intuitively regardless of absolute path.
        posix = Path(path).as_posix()
        return self._ignore_spec.match_file(posix)

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle a file modification event.

        Args:
            event: The watchdog event.
        """
        if not event.is_directory:
            self._handle(_event_path(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle a file creation event.

        Args:
            event: The watchdog event.
        """
        if not event.is_directory:
            self._handle(_event_path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle a file move/rename event.

        Args:
            event: The watchdog event.
        """
        if not event.is_directory:
            dest = getattr(event, "dest_path", None)
            self._handle(_event_path(dest if dest else event.src_path))


def _event_path(raw: str | bytes) -> str:
    """Normalize a watchdog event path to ``str``.

    Args:
        raw: Path as provided by watchdog (str or bytes).

    Returns:
        The path decoded to ``str``.
    """
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def watch_paths(
    paths: list[str],
    *,
    on_batch: Callable[[set[str]], None],
    debounce_ms: int = 300,
    ignore_patterns: list[str] | None = None,
    console: Console | None = None,
    stop_event: threading.Event | None = None,
    observer_factory: Callable[[], object] | None = None,
) -> None:
    """Watch paths and invoke ``on_batch`` with debounced change sets.

    Blocks until interrupted with Ctrl-C (or until ``stop_event`` is set),
    then shuts the observer down cleanly and flushes any pending batch.

    Args:
        paths: Files or directories to watch. Directories are watched
            recursively.
        on_batch: Called with the set of changed paths after each debounce
            interval settles.
        debounce_ms: Debounce interval in milliseconds.
        ignore_patterns: Gitignore-style patterns to exclude. Defaults to
            :data:`DEFAULT_IGNORE_PATTERNS`.
        console: Optional Rich console for status messages.
        stop_event: Optional externally-controlled stop signal. When omitted,
            a fresh event is created and the loop runs until KeyboardInterrupt.
        observer_factory: Optional factory returning a watchdog-Observer-like
            object. Injectable for tests; defaults to ``watchdog.Observer``.
    """
    ignore_spec = _build_ignore_spec(ignore_patterns or DEFAULT_IGNORE_PATTERNS)
    debouncer = Debouncer(callback=on_batch, delay_ms=debounce_ms)
    handler = LintroEventHandler(
        on_change=debouncer.on_change,
        ignore_spec=ignore_spec,
    )

    factory = observer_factory or Observer
    observer = factory()
    for path in paths:
        watch_target = path if Path(path).is_dir() else str(Path(path).parent)
        observer.schedule(handler, watch_target, recursive=True)

    observer.start()
    _emit_startup(console, paths)

    event = stop_event or threading.Event()
    try:
        while not event.is_set():
            # Wait in short slices so Ctrl-C is responsive on all platforms.
            event.wait(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        debouncer.flush()
        debouncer.cancel()
        if console is not None:
            console.print("\n[dim]Stopped watching.[/dim]")


def _emit_startup(console: Console | None, paths: list[str]) -> None:
    """Print the initial watch banner.

    Args:
        console: Optional Rich console.
        paths: Paths being watched.
    """
    if console is None:
        return
    joined = ", ".join(paths)
    console.print(f"👀 [bold]Watching for changes in[/bold] {joined}...")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
