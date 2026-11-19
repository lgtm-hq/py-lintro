"""Tests for dropping the events FSEvents replays at watch start (#3525)."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.watch.watcher import (
    DEFAULT_OBSERVER_REPLAYS_STARTUP,
    LintroEventHandler,
    StartupReplayFilter,
    _build_ignore_spec,
    watch_paths,
)

EventBuilder = Callable[..., Any]


def _filter_started_at(
    started_ns: int,
    *,
    expired: bool = False,
    stat: Callable[[str], Any] | None = None,
) -> StartupReplayFilter:
    """Build a filter with a fixed start and a controllable window.

    Args:
        started_ns: Wall-clock start in nanoseconds.
        expired: When True, the clock reads past the window's deadline.
        stat: Optional ``os.stat`` replacement.

    Returns:
        The configured filter.
    """
    extra: dict[str, Any] = {} if stat is None else {"stat": stat}
    return StartupReplayFilter(
        started_ns=started_ns,
        deadline=10.0,
        clock=lambda: 11.0 if expired else 9.0,
        **extra,
    )


def _saved_file(tmp_path: Path) -> tuple[Path, int]:
    """Write a file and return it with its inode change time.

    Args:
        tmp_path: Directory to write into.

    Returns:
        The file and its ``st_ctime_ns``.
    """
    saved = tmp_path / "saved.py"
    saved.write_text("x = 1\n", encoding="utf-8")
    return saved, saved.stat().st_ctime_ns


def test_file_changed_before_start_is_a_replay(tmp_path: Path) -> None:
    """A change time older than the start marks the event as a replay."""
    saved, changed_ns = _saved_file(tmp_path)

    assert_that(_filter_started_at(changed_ns + 1)(str(saved))).is_true()


def test_file_changed_at_start_is_kept(tmp_path: Path) -> None:
    """A change stamped at the start itself is not provably older, so it is kept."""
    saved, changed_ns = _saved_file(tmp_path)

    assert_that(_filter_started_at(changed_ns)(str(saved))).is_false()


def test_window_expiry_keeps_old_files(tmp_path: Path) -> None:
    """After the startup window nothing is treated as a replay."""
    saved, changed_ns = _saved_file(tmp_path)

    replay = _filter_started_at(changed_ns + 1, expired=True)

    assert_that(replay(str(saved))).is_false()


def test_missing_path_is_kept(tmp_path: Path) -> None:
    """A path that cannot be stat'ed cannot be proven old, so it is kept."""
    replay = _filter_started_at(2**62)

    assert_that(replay(str(tmp_path / "gone.py"))).is_false()


@pytest.mark.parametrize(
    "changed_ns",
    [
        pytest.param(1_700_000_000_000_000_000, id="whole-second-hfs-fat"),
        pytest.param(1_700_000_000_010_000_000, id="10ms-exfat"),
    ],
)
def test_coarse_change_time_is_kept(changed_ns: int) -> None:
    """Stamps from coarse filesystems may round a later change below the start.

    Args:
        changed_ns: Change time a coarse-granularity filesystem could report.
    """
    replay = _filter_started_at(
        changed_ns + 5_000_000_000,
        stat=lambda _path: SimpleNamespace(st_ctime_ns=changed_ns),
    )

    assert_that(replay("/proj/saved.py")).is_false()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Linux stamps files from a coarse clock that can lag time.time_ns(); "
    "the filter is only enabled for FSEvents",
)
def test_starting_now_anchors_at_the_current_time(tmp_path: Path) -> None:
    """A file written before the filter is built is a replay; one written after is not."""
    before, _ = _saved_file(tmp_path)
    replay = StartupReplayFilter.starting_now()
    after = tmp_path / "after.py"
    after.write_text("y = 1\n", encoding="utf-8")

    assert_that(replay(str(before))).is_true()
    assert_that(replay(str(after))).is_false()


def test_handler_drops_replayed_creates_and_modifies_but_not_moves(
    fake_fs_event: EventBuilder,
) -> None:
    """Moves are never checked: files in a renamed directory keep old stamps.

    Args:
        fake_fs_event: Builder for synthetic filesystem events.
    """
    seen: list[str] = []
    handler = LintroEventHandler(
        on_change=seen.append,
        ignore_spec=_build_ignore_spec([]),
        is_startup_replay=lambda _path: True,
    )

    handler.on_created(fake_fs_event("/proj/created.py"))
    handler.on_modified(fake_fs_event("/proj/modified.py"))
    handler.on_moved(
        fake_fs_event("/proj/old/moved.py", dest_path="/proj/new/moved.py"),
    )

    assert_that(seen).is_equal_to(["/proj/new/moved.py"])


class _ReplayingObserver:
    """Fake observer that delivers modify events for given files on start."""

    def __init__(self, files: list[Path], on_start: Callable[[], None]) -> None:
        """Initialize the observer.

        Args:
            files: Files to report as modified once started.
            on_start: Called after the events are delivered.
        """
        self._files = files
        self._on_start = on_start
        self._handler: Any = None

    def schedule(self, handler: Any, path: str, *, recursive: bool = False) -> None:
        """Keep the handler so events can be delivered to it.

        Args:
            handler: Event handler from ``watch_paths``.
            path: Watched directory (unused).
            recursive: Whether the watch is recursive (unused).
        """
        self._handler = handler

    def start(self) -> None:
        """Deliver one modify event per file, then run the start hook."""
        for path in self._files:
            self._handler.on_modified(
                SimpleNamespace(is_directory=False, src_path=str(path)),
            )
        self._on_start()

    def stop(self) -> None:
        """Nothing to stop."""

    def join(self, timeout: float | None = None) -> None:
        """Nothing to join.

        Args:
            timeout: Unused.
        """


def _watch_with_replay(
    tmp_path: Path,
    *,
    drop_startup_replay: bool | None,
) -> list[set[str]]:
    """Watch a directory whose file was saved before start, plus one written after.

    Args:
        tmp_path: Directory to watch.
        drop_startup_replay: Value passed through to ``watch_paths``.

    Returns:
        Batches flushed when the watch stops.
    """
    saved, _ = _saved_file(tmp_path)
    live = tmp_path / "live.py"
    stop = threading.Event()
    batches: list[set[str]] = []

    def _write_live_then_stop() -> None:
        live.write_text("y = 1\n", encoding="utf-8")
        observer._handler.on_modified(
            SimpleNamespace(is_directory=False, src_path=str(live)),
        )
        stop.set()

    observer = _ReplayingObserver([saved], _write_live_then_stop)
    watch_paths(
        [str(tmp_path)],
        on_batch=batches.append,
        debounce_ms=50_000,  # long, so only the shutdown flush emits
        stop_event=stop,
        observer_factory=lambda: observer,
        drop_startup_replay=drop_startup_replay,
    )
    return batches


def test_watch_paths_drops_replay_and_keeps_live_change(tmp_path: Path) -> None:
    """With the filter on, only the change made after start reaches a batch."""
    batches = _watch_with_replay(tmp_path, drop_startup_replay=True)

    assert_that(batches).is_equal_to([{str(tmp_path / "live.py")}])


def test_watch_paths_with_custom_observer_keeps_replay_by_default(
    tmp_path: Path,
) -> None:
    """An injected observer gets the old behaviour unless the caller opts in."""
    batches = _watch_with_replay(tmp_path, drop_startup_replay=None)

    assert_that(batches).is_equal_to(
        [{str(tmp_path / "saved.py"), str(tmp_path / "live.py")}],
    )


def test_only_the_macos_default_observer_replays() -> None:
    """Linux (inotify) and Windows keep their behaviour unchanged."""
    assert_that(DEFAULT_OBSERVER_REPLAYS_STARTUP).is_equal_to(
        sys.platform == "darwin",
    )
