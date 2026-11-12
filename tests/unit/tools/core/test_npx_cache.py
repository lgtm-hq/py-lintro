"""lintro's per-pin npx cache: validation, repair and npm-compatible locking.

An ``_npx`` entry that would not run exactly the pin is deleted before npx
runs, under the same ``concurrency.lock`` directory npm itself takes (#2043).
Real-npx coverage is in ``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import json
import os
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
