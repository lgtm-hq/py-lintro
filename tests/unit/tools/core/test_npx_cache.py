"""lintro's per-pin npx cache: validation, repair and npm-compatible locking.

An ``_npx`` entry that would not run exactly the pin is deleted before npx
runs, under the same ``concurrency.lock`` directory npm itself takes (#2043).
Real-npx coverage is in ``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - runs this interpreter with a fixed argv; shell=False
import sys
import threading
import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core import npx_cache
from lintro.tools.core.npx_cache import (
    NpxCacheError,
    check_cache_location,
    entry_runs_pin,
    npx_cache_dir,
    repair_npx_cache,
)
from lintro.utils import file_cache

_PIN = "3.9.4"
_SPEC = f"prettier@{_PIN}"


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Lintro's per-pin cache for prettier, under a private cache root.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped override of lintro's cache root.

    Returns:
        The per-pin cache directory (not yet created).
    """
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / "lintro-cache")
    return npx_cache_dir(_SPEC)


def _entry(
    cache: Path,
    name: str = "0123456789abcdef",
    *,
    version: str = _PIN,
    package_name: str = "prettier",
    bin_target: str | None = "../prettier/bin.js",
) -> Path:
    """Create an ``_npx`` entry the way npm lays one out.

    Args:
        cache: Per-pin cache.
        name: Entry (hash) directory name.
        version: Version written into the top-level package.
        package_name: Name written into the top-level package.
        bin_target: ``.bin/prettier`` link target, or None for no link.

    Returns:
        The entry directory.
    """
    entry = cache / "_npx" / name
    package = entry / "node_modules" / "prettier"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": package_name, "version": version}),
        encoding="utf-8",
    )
    (package / "bin.js").write_text("", encoding="utf-8")
    if bin_target is not None:
        bin_dir = entry / "node_modules" / ".bin"
        bin_dir.mkdir()
        (bin_dir / "prettier").symlink_to(bin_target)
    return entry


def _runs_pin(entry: Path) -> bool:
    return entry_runs_pin(entry, package="prettier", pin=_PIN, binary="prettier")


def _repair(cache: Path) -> None:
    repair_npx_cache(
        cache,
        package="prettier",
        pin=_PIN,
        binary="prettier",
        pathsep=":",
    )


# ---------------------------------------------------------------------------
# entry_runs_pin
# ---------------------------------------------------------------------------


def test_installed_pin_is_a_valid_entry(cache: Path) -> None:
    """Npm's own layout for the pin passes.

    Args:
        cache: Per-pin cache.
    """
    assert_that(_runs_pin(_entry(cache))).is_true()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": "1.0.0"},
        {"version": f"{_PIN}-rc.1"},
        {"package_name": "not-prettier"},
        {"bin_target": None},
    ],
    ids=["older-version", "prerelease", "wrong-name", "missing-bin"],
)
def test_entry_that_would_not_run_the_pin_is_invalid(
    cache: Path,
    kwargs: dict[str, object],
) -> None:
    """Version, name and the executable link must all match the pin.

    Args:
        cache: Per-pin cache.
        kwargs: How the entry deviates from the pin.
    """
    assert_that(_runs_pin(_entry(cache, **kwargs))).is_false()  # type: ignore[arg-type]


def test_bin_pointing_at_another_package_is_invalid(cache: Path) -> None:
    """``.bin/prettier`` must resolve inside the top-level prettier.

    Args:
        cache: Per-pin cache.
    """
    entry = _entry(cache, bin_target="../older/bin.js")
    older = entry / "node_modules" / "older"
    older.mkdir()
    (older / "bin.js").write_text("", encoding="utf-8")

    assert_that(_runs_pin(entry)).is_false()


def test_symlinked_package_is_invalid(cache: Path, tmp_path: Path) -> None:
    """Npm installs registry packages as real directories.

    Args:
        cache: Per-pin cache.
        tmp_path: Temporary directory provided by pytest.
    """
    entry = _entry(cache)
    real = entry / "node_modules" / "prettier"
    moved = tmp_path / "moved-prettier"
    real.rename(moved)
    real.symlink_to(moved)

    assert_that(_runs_pin(entry)).is_false()


@pytest.mark.parametrize(
    "content",
    ["{broken", "[1]"],
    ids=["invalid-json", "not-object"],
)
def test_unreadable_manifest_is_invalid(cache: Path, content: str) -> None:
    """A manifest that cannot be read is not evidence of the pin.

    Args:
        cache: Per-pin cache.
        content: Broken ``package.json`` content.
    """
    entry = _entry(cache)
    (entry / "node_modules" / "prettier" / "package.json").write_text(
        content,
        encoding="utf-8",
    )

    assert_that(_runs_pin(entry)).is_false()


# ---------------------------------------------------------------------------
# repair_npx_cache
# ---------------------------------------------------------------------------


def test_repair_keeps_a_valid_entry_untouched(cache: Path) -> None:
    """A warm, correct cache is reused: same files, no reinstall.

    Args:
        cache: Per-pin cache.
    """
    entry = _entry(cache)
    manifest = entry / "node_modules" / "prettier" / "package.json"
    inode = manifest.stat().st_ino

    _repair(cache)

    assert_that(manifest.stat().st_ino).is_equal_to(inode)
    assert_that((entry / "concurrency.lock").exists()).is_false()


def test_repair_deletes_every_entry_that_would_not_run_the_pin(cache: Path) -> None:
    """Bad entries go, good ones stay, and nothing is left half-moved.

    Args:
        cache: Per-pin cache.
    """
    good = _entry(cache, "good")
    older = _entry(cache, "older", version="1.0.0")
    unlinked = _entry(cache, "unlinked", bin_target=None)
    empty = cache / "_npx" / "empty"
    empty.mkdir()
    stray = cache / "_npx" / "stray-file"
    stray.write_text("", encoding="utf-8")

    _repair(cache)

    assert_that(sorted(p.name for p in (cache / "_npx").iterdir())).is_equal_to(
        [good.name],
    )
    for gone in (older, unlinked, empty, stray):
        assert_that(gone.exists()).is_false()


def test_repair_replaces_a_symlinked_npx_directory(cache: Path, tmp_path: Path) -> None:
    """An ``_npx`` link to somewhere else is removed; npm recreates it.

    Args:
        cache: Per-pin cache.
        tmp_path: Temporary directory provided by pytest.
    """
    foreign = tmp_path / "foreign-npx"
    foreign.mkdir()
    cache.mkdir(parents=True)
    (cache / "_npx").symlink_to(foreign)

    _repair(cache)

    assert_that((cache / "_npx").exists()).is_false()
    assert_that(foreign.exists()).is_true()


def test_repair_removes_a_symlinked_entry(cache: Path, tmp_path: Path) -> None:
    """An entry that is a link is not trusted, wherever it points.

    Args:
        cache: Per-pin cache.
        tmp_path: Temporary directory provided by pytest.
    """
    target = _entry(tmp_path / "other-cache")
    (cache / "_npx").mkdir(parents=True)
    link = cache / "_npx" / "linked"
    link.symlink_to(target)

    _repair(cache)

    assert_that(link.exists() or link.is_symlink()).is_false()
    assert_that(target.exists()).is_true()


def test_cache_outside_lintros_root_is_refused(cache: Path, tmp_path: Path) -> None:
    """A per-pin cache that resolves elsewhere raises before anything is read.

    Args:
        cache: Per-pin cache.
        tmp_path: Temporary directory provided by pytest.
    """
    elsewhere = tmp_path / "elsewhere"
    _entry(elsewhere.parent / "elsewhere-cache")
    elsewhere.mkdir()
    cache.parent.mkdir(parents=True)
    cache.symlink_to(elsewhere)

    with pytest.raises(NpxCacheError, match="outside"):
        _repair(cache)


@pytest.mark.parametrize(
    ("name", "pathsep", "refused"),
    [("a:b", ":", True), ("a;b", ";", True), ("a:b", ";", False)],
    ids=["posix", "windows", "windows-drive-colon"],
)
def test_only_the_platform_separator_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    pathsep: str,
    refused: bool,
) -> None:
    """The check uses the separator of the platform npx runs on.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped override of lintro's cache root.
        name: Cache root directory name.
        pathsep: Simulated platform separator.
        refused: Whether the location must be refused.
    """
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / name)
    location = npx_cache_dir(_SPEC)

    if refused:
        with pytest.raises(NpxCacheError, match="PATH separator"):
            check_cache_location(location, pathsep=pathsep)
    else:
        assert_that(check_cache_location(location, pathsep=pathsep)).is_equal_to(
            Path(os.path.realpath(location)),
        )


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


def test_stale_lock_left_by_a_dead_process_is_reclaimed(cache: Path) -> None:
    """A lock npm itself would call abandoned does not block repair.

    Args:
        cache: Per-pin cache.
    """
    older = _entry(cache, version="1.0.0")
    lock = older / "concurrency.lock"
    lock.mkdir()
    old = time.time() - 120
    os.utime(lock, (old, old))

    _repair(cache)

    assert_that(older.exists()).is_false()


def test_live_lock_refuses_after_waiting(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock held by a running npx is waited for, then reported.

    Args:
        cache: Per-pin cache.
        monkeypatch: Scoped override of the wait budget.
    """
    entry = _entry(cache, version="1.0.0")
    (entry / "concurrency.lock").mkdir()
    monkeypatch.setattr(npx_cache, "_LOCK_WAIT_SECONDS", 0.2)

    with pytest.raises(NpxCacheError, match="stayed locked"):
        _repair(cache)

    assert_that(entry.exists()).is_true()


def test_repair_waits_for_a_lock_released_by_npx(cache: Path) -> None:
    """Validation never runs while npx holds the entry's lock.

    Args:
        cache: Per-pin cache.
    """
    entry = _entry(cache, version="1.0.0")
    lock = entry / "concurrency.lock"
    lock.mkdir()
    released: list[float] = []

    def release() -> None:
        time.sleep(0.3)
        released.append(time.monotonic())
        lock.rmdir()

    holder = threading.Thread(target=release)
    holder.start()
    _repair(cache)
    finished = time.monotonic()
    holder.join()

    assert_that(entry.exists()).is_false()
    assert_that(finished).is_greater_than_or_equal_to(released[0])


def test_concurrent_repairs_agree(cache: Path) -> None:
    """Several lintro runs repairing at once keep the good entry, drop the bad.

    Args:
        cache: Per-pin cache.
    """
    good = _entry(cache, "good")
    bad = [_entry(cache, f"bad{i}", version="1.0.0") for i in range(5)]
    errors: list[Exception] = []

    def run() -> None:
        try:
            _repair(cache)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert_that(errors).is_empty()
    assert_that(good.exists()).is_true()
    assert_that([entry.exists() for entry in bad]).does_not_contain(True)
    assert_that(_runs_pin(good)).is_true()


# ---------------------------------------------------------------------------
# Concurrent repair stress (#3419)
# ---------------------------------------------------------------------------

#: Longest ``_npx`` name a repair may create: the stale prefix, the original
#: base name (at most 64 characters here) and a uuid, far below ``NAME_MAX``.
_ASIDE_NAME_BOUND = 128

#: Child repairer: waits for the start signal, then repairs a few times.
_CHILD_REPAIRER = """
import sys, time
from pathlib import Path
from lintro.utils import file_cache
from lintro.tools.core.npx_cache import repair_npx_cache
file_cache.CACHE_DIR = Path(sys.argv[1])
go = Path(sys.argv[3])
while not go.exists():
    time.sleep(0.005)
for _ in range(int(sys.argv[4])):
    repair_npx_cache(
        Path(sys.argv[2]), package="prettier", pin=sys.argv[5],
        binary="prettier", pathsep=":",
    )
"""


def _bulky_bad_entry(cache: Path, name: str, *, files: int = 150) -> Path:
    """Create a wrong-version entry slow enough to delete to widen races.

    Args:
        cache: Per-pin cache.
        name: Entry (hash) directory name.
        files: Filler files written into the entry.

    Returns:
        The entry directory.
    """
    entry = _entry(cache, name, version="1.0.0")
    filler = entry / "node_modules" / "filler"
    filler.mkdir()
    for index in range(files):
        (filler / f"f{index}.js").write_text("x", encoding="utf-8")
    return entry


class _NameWatcher(threading.Thread):
    """Record the longest name seen under ``_npx`` while repairs run."""

    def __init__(self, npx_root: Path) -> None:
        """Watch *npx_root* until stopped.

        Args:
            npx_root: ``<cache>/_npx``.
        """
        super().__init__(daemon=True)
        self.npx_root = npx_root
        self.longest = 0
        self.done = threading.Event()

    def run(self) -> None:
        """Poll the directory listing until :attr:`done` is set."""
        while not self.done.is_set():
            try:
                names = [entry.name for entry in self.npx_root.iterdir()]
            except OSError:
                names = []
            self.longest = max([self.longest, *map(len, names)])


class _Repairer(threading.Thread):
    """Repair the cache a few times once released, keeping any error."""

    def __init__(self, cache: Path, start: threading.Event, rounds: int) -> None:
        """Prepare a repairer thread.

        Args:
            cache: Per-pin cache.
            start: Released when every repairer is ready.
            rounds: Repairs to run back to back.
        """
        super().__init__()
        self.cache = cache
        self.release = start
        self.rounds = rounds
        self.errors: list[Exception] = []

    def run(self) -> None:
        """Run the repairs, recording rather than raising failures."""
        self.release.wait()
        for _ in range(self.rounds):
            try:
                _repair(self.cache)
            except Exception as exc:
                self.errors.append(exc)


def _remaining(cache: Path) -> list[str]:
    """List what is left under ``_npx``.

    Args:
        cache: Per-pin cache.

    Returns:
        Sorted entry names.
    """
    return sorted(entry.name for entry in (cache / "_npx").iterdir())


def test_many_threads_repairing_keep_names_bounded(cache: Path) -> None:
    """Racing repairers in one process never grow a name or fail (#3419).

    Args:
        cache: Per-pin cache.
    """
    good = _entry(cache, "good")
    for index in range(6):
        _bulky_bad_entry(cache, f"bad{index}")
    watcher = _NameWatcher(cache / "_npx")
    watcher.start()
    start = threading.Event()
    repairers = [_Repairer(cache, start, rounds=4) for _ in range(16)]
    for repairer in repairers:
        repairer.start()
    start.set()
    for repairer in repairers:
        repairer.join()
    watcher.done.set()
    watcher.join()

    assert_that([e for r in repairers for e in r.errors]).is_empty()
    assert_that(watcher.longest).is_less_than_or_equal_to(_ASIDE_NAME_BOUND)
    assert_that(_remaining(cache)).is_equal_to([good.name])
    assert_that(_runs_pin(good)).is_true()


def test_many_processes_repairing_keep_names_bounded(
    cache: Path,
    tmp_path: Path,
) -> None:
    """Separate lintro runs repairing one cache never fail or leave debris.

    Args:
        cache: Per-pin cache.
        tmp_path: Temporary directory provided by pytest.
    """
    good = _entry(cache, "good")
    for index in range(6):
        _bulky_bad_entry(cache, f"bad{index}")
    go = tmp_path / "go"
    watcher = _NameWatcher(cache / "_npx")
    watcher.start()
    children = [
        subprocess.Popen(  # nosec B603 - fixed argv, shell=False
            [
                sys.executable,
                "-c",
                _CHILD_REPAIRER,
                str(file_cache.CACHE_DIR),
                str(cache),
                str(go),
                "3",
                _PIN,
            ],
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    time.sleep(0.5)
    go.touch()
    failures = [
        stderr
        for child in children
        for _, stderr in [child.communicate(timeout=90)]
        if child.returncode != 0
    ]
    watcher.done.set()
    watcher.join()

    assert_that(failures).is_empty()
    assert_that(watcher.longest).is_less_than_or_equal_to(_ASIDE_NAME_BOUND)
    assert_that(_remaining(cache)).is_equal_to([good.name])


def test_stale_entry_is_deleted_without_another_rename(cache: Path) -> None:
    """An entry already set aside is removed in place, however long its name.

    Renaming it again would prepend the prefix once more; at the file name
    limit that rename fails and the tool would be refused for nothing.

    Args:
        cache: Per-pin cache.
    """
    good = _entry(cache, "good")
    stale = _entry(cache, ".lintro-stale-" + "a" * 230, version="1.0.0")

    _repair(cache)

    assert_that(stale.exists()).is_false()
    assert_that(_remaining(cache)).is_equal_to([good.name])


def test_entry_with_a_long_name_is_still_repaired(cache: Path) -> None:
    """The aside name stays bounded whatever the original name's length.

    Args:
        cache: Per-pin cache.
    """
    long_bad = _entry(cache, "b" * 240, version="1.0.0")

    _repair(cache)

    assert_that(long_bad.exists()).is_false()
    assert_that((cache / "_npx").is_dir()).is_true()
    assert_that(_remaining(cache)).is_empty()
