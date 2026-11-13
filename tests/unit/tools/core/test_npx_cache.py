"""lintro's per-pin npx cache: validation, repair and npm-compatible locking.

An ``_npx`` entry that would not run exactly the pin is deleted before npx
runs, under the same ``concurrency.lock`` directory npm itself takes (#2043).
Real-npx coverage is in ``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import contextlib
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

#: A path argument as ``os.replace`` and ``os.unlink`` accept one.
_PathArg = os.PathLike[str] | str


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
            time.sleep(0.001)


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


# ---------------------------------------------------------------------------
# Locks and leftovers around a repair
# ---------------------------------------------------------------------------


def test_repair_never_deletes_a_lock_npx_took_on_a_recreated_entry(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the bad entry is aside, its old lock path belongs to whoever is there.

    npx recreates the entry and takes its own lock while the aside copy is
    still being deleted; that lock must survive the repair.

    Args:
        cache: Per-pin cache.
        monkeypatch: Hooks npx's recreation into the aside deletion.
    """
    bad = _entry(cache, version="1.0.0")
    npx_lock = bad / "concurrency.lock"
    delete = npx_cache._delete

    def npx_recreates_then_delete(path: Path) -> None:
        npx_lock.mkdir(parents=True)
        delete(path)

    monkeypatch.setattr(npx_cache, "_delete", npx_recreates_then_delete)

    _repair(cache)

    assert_that(npx_lock.is_dir()).is_true()
    assert_that([n for n in _remaining(cache) if n.startswith(".")]).is_empty()


def test_repair_never_moves_an_entry_npx_is_installing(
    cache: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stray file that npx replaces with a fresh, locked install is left alone.

    The repair sees a file, but by the time it removes it npx has put a
    directory there and holds its lock: the install must not be moved while
    it is written, and the repair waits for it and then keeps it.

    Args:
        cache: Per-pin cache.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Hooks npx's install into the removal of the stray file.
    """
    (cache / "_npx").mkdir(parents=True)
    entry = cache / "_npx" / "0123456789abcdef"
    entry.write_text("", encoding="utf-8")
    real_replace, real_unlink = os.replace, os.unlink
    installs: list[threading.Thread] = []

    staged = _entry(tmp_path / "staging", entry.name)

    def npx_installs() -> None:
        time.sleep(0.3)
        (staged / "node_modules").rename(entry / "node_modules")
        (entry / "concurrency.lock").rmdir()

    def race(path: _PathArg) -> None:
        if Path(path) == entry and not installs:
            real_unlink(entry)
            (entry / "concurrency.lock").mkdir(parents=True)
            installs.append(threading.Thread(target=npx_installs))
            installs[0].start()

    def racing_replace(src: _PathArg, dst: _PathArg) -> None:
        race(src)
        real_replace(src, dst)

    def racing_unlink(path: _PathArg, *, dir_fd: int | None = None) -> None:
        race(path)
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "replace", racing_replace)
    monkeypatch.setattr(os, "unlink", racing_unlink)
    try:
        _repair(cache)
    finally:
        for install in installs:
            install.join()

    assert_that(installs).is_length(1)
    assert_that(_runs_pin(entry)).is_true()
    assert_that(_remaining(cache)).is_equal_to([entry.name])


def test_repair_removes_an_npx_directory_left_aside_beside_it(cache: Path) -> None:
    """A whole ``_npx`` moved aside by an earlier run is deleted from the cache.

    Args:
        cache: Per-pin cache.
    """
    good = _entry(cache, "good")
    leftover = cache / ".lintro-stale-_npx-0123456789abcdef0123456789abcdef"
    (leftover / "0123456789abcdef" / "node_modules").mkdir(parents=True)

    _repair(cache)

    assert_that(leftover.exists()).is_false()
    assert_that(sorted(p.name for p in cache.iterdir())).is_equal_to(["_npx"])
    assert_that(_runs_pin(good)).is_true()


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that bind the current user",
)
def test_undeletable_leftover_warns_and_the_valid_entry_still_runs(
    cache: Path,
    logged_warnings: list[str],
) -> None:
    """A leftover that cannot be deleted is reported, never a refusal.

    It no longer has an npm entry name, so it cannot change what runs.

    Args:
        cache: Per-pin cache.
        logged_warnings: Warnings logged during the test.
    """
    good = _entry(cache, "good")
    leftover = cache / "_npx" / ".lintro-stale-protected-0123"
    sealed = leftover / "sealed"
    sealed.mkdir(parents=True)
    (sealed / "kept.js").write_text("", encoding="utf-8")
    sealed.chmod(0o555)
    try:
        _repair(cache)
    finally:
        sealed.chmod(0o755)

    assert_that(_runs_pin(good)).is_true()
    assert_that(leftover.exists()).is_true()
    assert_that(logged_warnings).is_length(1)
    assert_that(logged_warnings[0]).contains(str(leftover), "kept.js")


# ---------------------------------------------------------------------------
# Lock ownership: refuse or leak, never remove a lock lintro does not hold (#3422)
# ---------------------------------------------------------------------------


def _owner_marker(pid: int) -> str:
    """Name an owner marker as a lintro process *pid* on this host writes it.

    Args:
        pid: Process id recorded in the marker.

    Returns:
        The marker file name.
    """
    return f"{npx_cache._OWNER_PREFIX}{npx_cache._host_tag()}-{pid}-{'0' * 32}"


def _exited_pid() -> int:
    """Return the id of a process that has already exited.

    Returns:
        A pid no live process holds.
    """
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # nosec B603
    child.wait()
    return child.pid


def _npm_reclaims_at_claim(
    monkeypatch: pytest.MonkeyPatch,
    lock: Path,
) -> list[int]:
    """Make "npm" reclaim *lock* just before lintro renames it to claim it.

    Like npm, the stand-in removes the stale lock with ``rmdir`` and takes a
    fresh one with ``mkdir``.

    Args:
        monkeypatch: Installs the hook on ``os.rename``.
        lock: The stale lock lintro is about to claim.

    Returns:
        A list that receives the inode of npm's fresh lock.
    """
    taken: list[int] = []
    real_rename = os.rename

    def racing_rename(src: _PathArg, dst: _PathArg) -> None:
        if not taken and Path(src) == lock:
            lock.rmdir()
            lock.mkdir()
            taken.append(lock.stat().st_ino)
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", racing_rename)
    return taken


def test_lock_lintro_holds_survives_npms_stale_lock_removal(tmp_path: Path) -> None:
    """Npm removes a stale lock with ``rmdir``, which cannot remove lintro's.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    lock = tmp_path / "concurrency.lock"
    marker = npx_cache._acquire(lock)
    old = time.time() - 120
    os.utime(lock, (old, old))

    with pytest.raises(OSError):
        lock.rmdir()
    held = npx_cache._holds(marker)
    npx_cache._release(marker)

    assert_that(held).is_true()
    assert_that(sorted(p.name for p in tmp_path.iterdir())).is_empty()


def test_live_lock_moved_by_a_stale_claim_is_put_back(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Npm reclaiming first keeps its lock: lintro restores it, then waits.

    Args:
        cache: Per-pin cache.
        monkeypatch: Places npm's reclaim inside lintro's claim.
    """
    bad = _entry(cache, version="1.0.0")
    lock = bad / "concurrency.lock"
    lock.mkdir()
    old = time.time() - 120
    os.utime(lock, (old, old))
    taken = _npm_reclaims_at_claim(monkeypatch, lock)
    seen_at_release: list[int] = []

    def npm_releases() -> None:
        deadline = time.monotonic() + 10
        while not taken and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)
        with contextlib.suppress(OSError):
            seen_at_release.append(lock.stat().st_ino)
            lock.rmdir()

    holder = threading.Thread(target=npm_releases)
    holder.start()
    try:
        _repair(cache)
    finally:
        holder.join()

    assert_that(taken).is_length(1)
    assert_that(seen_at_release).is_equal_to(taken)
    assert_that(bad.exists()).is_false()


def test_live_lock_that_cannot_be_put_back_is_left_and_repair_refused(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the stale-lock race without a way back leaks the lock and refuses.

    Args:
        cache: Per-pin cache.
        monkeypatch: Places npm's reclaim inside lintro's claim and takes
            away the exclusive rename.
    """
    bad = _entry(cache, version="1.0.0")
    lock = bad / "concurrency.lock"
    lock.mkdir()
    old = time.time() - 120
    os.utime(lock, (old, old))
    taken = _npm_reclaims_at_claim(monkeypatch, lock)
    create = npx_cache._create_lock

    def no_way_back(src: Path, dst: Path) -> bool | None:
        return None

    def create_then_lose_exclusive_rename(path: Path) -> Path | None:
        marker = create(path)
        monkeypatch.setattr(npx_cache, "_rename_noreplace", no_way_back)
        return marker

    monkeypatch.setattr(npx_cache, "_create_lock", create_then_lose_exclusive_rename)

    with pytest.raises(NpxCacheError, match="could not put it back"):
        _repair(cache)

    leaked = [p for p in bad.iterdir() if p.name.startswith(".lintro-stale-")]
    assert_that(lock.exists()).is_false()
    assert_that([p.stat().st_ino for p in leaked]).is_equal_to(taken)
    assert_that(_runs_pin(bad)).is_false()
    assert_that((bad / "node_modules").is_dir()).is_true()


def test_lock_left_by_an_exited_lintro_is_taken_over(cache: Path) -> None:
    """A lock whose owner process has exited does not block repair.

    Args:
        cache: Per-pin cache.
    """
    bad = _entry(cache, version="1.0.0")
    lock = bad / "concurrency.lock"
    lock.mkdir()
    (lock / _owner_marker(_exited_pid())).touch()

    _repair(cache)

    assert_that(bad.exists()).is_false()
    assert_that(_remaining(cache)).is_empty()


def test_stale_lock_of_a_live_lintro_is_waited_for_then_refused(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Age alone never lets one lintro take another live lintro's lock.

    Args:
        cache: Per-pin cache.
        monkeypatch: Scoped override of the wait budget.
    """
    bad = _entry(cache, version="1.0.0")
    lock = bad / "concurrency.lock"
    lock.mkdir()
    owner = lock / _owner_marker(os.getppid())
    owner.touch()
    old = time.time() - 120
    os.utime(lock, (old, old))
    monkeypatch.setattr(npx_cache, "_LOCK_WAIT_SECONDS", 0.2)

    with pytest.raises(NpxCacheError, match="stayed locked"):
        _repair(cache)

    assert_that(owner.is_file()).is_true()
    assert_that((bad / "node_modules").is_dir()).is_true()


def test_repair_refuses_and_leaves_the_lock_when_ownership_is_lost(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock lintro no longer holds is neither moved with the entry nor removed.

    Args:
        cache: Per-pin cache.
        monkeypatch: Hands the lock to another holder during validation.
    """
    bad = _entry(cache, version="1.0.0")
    lock = bad / "concurrency.lock"
    validate = npx_cache.entry_runs_pin

    def validate_while_another_holder_takes_over(entry: Path, **kwargs: str) -> bool:
        for marker in lock.iterdir():
            marker.unlink()
        return validate(entry, **kwargs)

    monkeypatch.setattr(
        npx_cache,
        "entry_runs_pin",
        validate_while_another_holder_takes_over,
    )

    with pytest.raises(NpxCacheError, match="lost its lock"):
        _repair(cache)

    assert_that(lock.is_dir()).is_true()
    assert_that((bad / "node_modules").is_dir()).is_true()


def test_repair_without_an_exclusive_rename_still_locks_and_cleans_up(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platforms with no exclusive rename fall back to ``mkdir`` then mark.

    Args:
        cache: Per-pin cache.
        monkeypatch: Takes away the exclusive rename.
    """
    good = _entry(cache, "good")
    bad = _entry(cache, "bad", version="1.0.0")
    before = sorted(p.name for p in good.iterdir())

    def unsupported(src: Path, dst: Path) -> bool | None:
        return None

    monkeypatch.setattr(npx_cache, "_rename_noreplace", unsupported)

    _repair(cache)

    assert_that(bad.exists()).is_false()
    assert_that(sorted(p.name for p in good.iterdir())).is_equal_to(before)
    assert_that(_runs_pin(good)).is_true()
