"""Tests for lintro.utils.file_cache module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.file_cache import (
    CACHE_FORMAT_VERSION,
    FileFingerprint,
    ToolCache,
    clear_all_caches,
    get_cache_stats,
)


def test_file_fingerprint_to_dict() -> None:
    """Convert fingerprint to dictionary."""
    fp = FileFingerprint(path="/test/file.py", mtime=1234567890.0, size=1024)
    result = fp.to_dict()

    assert_that(result).is_equal_to(
        {
            "path": "/test/file.py",
            "mtime": 1234567890.0,
            "size": 1024,
            "ctime_ns": 0,
            "mode": 0,
        },
    )


def test_file_fingerprint_from_dict() -> None:
    """Create fingerprint from dictionary."""
    data = {
        "path": "/test/file.py",
        "mtime": 1234567890.0,
        "size": 1024,
        "ctime_ns": 7,
        "mode": 0o100644,
    }
    fp = FileFingerprint.from_dict(data)

    assert_that(fp.path).is_equal_to("/test/file.py")
    assert_that(fp.mtime).is_equal_to(1234567890.0)
    assert_that(fp.size).is_equal_to(1024)


def test_file_fingerprint_roundtrip() -> None:
    """Roundtrip through to_dict and from_dict."""
    original = FileFingerprint(path="/test/file.py", mtime=1234567890.0, size=1024)
    result = FileFingerprint.from_dict(original.to_dict())

    assert_that(result.path).is_equal_to(original.path)
    assert_that(result.mtime).is_equal_to(original.mtime)
    assert_that(result.size).is_equal_to(original.size)


def test_tool_cache_empty_returns_all_files_as_changed(tmp_path: Path) -> None:
    """Empty cache returns all files as changed.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    temp_file = tmp_path / "test.py"
    temp_file.write_text("test content")

    cache = ToolCache(tool_name="test")
    changed = cache.get_changed_files([str(temp_file)])
    assert_that(changed).contains(str(temp_file))


def test_tool_cache_unchanged_file_not_returned(tmp_path: Path) -> None:
    """File in cache with the same fingerprint not returned as changed.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    temp_file = tmp_path / "test.py"
    temp_file.write_text("test content")

    cache = ToolCache(tool_name="test")
    cache.fingerprints[str(temp_file)] = FileFingerprint.of(
        str(temp_file),
        temp_file.stat(),
    )
    changed = cache.get_changed_files([str(temp_file)])
    assert_that(changed).does_not_contain(str(temp_file))


def test_tool_cache_modified_file_returned(tmp_path: Path) -> None:
    """File in cache with different mtime returned as changed.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    temp_file = tmp_path / "test.py"
    temp_file.write_text("test content")

    stat = temp_file.stat()
    cache = ToolCache(tool_name="test")
    cache.fingerprints[str(temp_file)] = FileFingerprint(
        path=str(temp_file),
        mtime=stat.st_mtime - 100,
        size=stat.st_size,
    )
    changed = cache.get_changed_files([str(temp_file)])
    assert_that(changed).contains(str(temp_file))


def test_tool_cache_size_changed_file_returned(tmp_path: Path) -> None:
    """File in cache with different size returned as changed.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    temp_file = tmp_path / "test.py"
    temp_file.write_text("test content")

    stat = temp_file.stat()
    cache = ToolCache(tool_name="test")
    cache.fingerprints[str(temp_file)] = FileFingerprint(
        path=str(temp_file),
        mtime=stat.st_mtime,
        size=stat.st_size + 100,  # Different size
    )
    changed = cache.get_changed_files([str(temp_file)])
    assert_that(changed).contains(str(temp_file))


def test_tool_cache_nonexistent_file_skipped() -> None:
    """Nonexistent file skipped in get_changed_files."""
    cache = ToolCache(tool_name="test")
    changed = cache.get_changed_files(["/nonexistent/file.py"])
    assert_that(changed).is_empty()


def test_tool_cache_record_keeps_checked_and_forgets_unchecked() -> None:
    """Record adds the checked files and drops the handed, unproven ones."""
    old = FileFingerprint(path="/p/a.py", mtime=1.5, size=1)
    new = FileFingerprint(path="/p/b.py", mtime=2.5, size=2)
    cache = ToolCache(tool_name="test", fingerprints={"/p/a.py": old})

    cache.record({"/p/b.py": new}, unchecked=["/p/a.py", "/p/b.py"])

    assert_that(cache.fingerprints).is_equal_to({"/p/b.py": new})


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "t", "fingerprints": {"/p/a.py": {}}},
        {"format_version": 1, "tool_name": "t", "fingerprints": {}},
        ["not", "an", "object"],
    ],
    ids=["unversioned", "version-1", "not-an-object"],
)
def test_tool_cache_load_discards_a_file_in_another_format(
    tmp_path: Path,
    payload: object,
) -> None:
    """A cache written by an earlier lintro is discarded, not trusted.

    Args:
        tmp_path: Pytest fixture for temporary directory.
        payload: Cache file contents in a format this version does not write.
    """
    entry = {"path": "/p/a.py", "mtime": 1.5, "size": 1}
    if isinstance(payload, dict) and "/p/a.py" in payload["fingerprints"]:
        payload["fingerprints"]["/p/a.py"] = entry
    (tmp_path / "t.json").write_text(json.dumps(payload))

    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        loaded = ToolCache.load("t")

    assert_that(loaded.fingerprints).is_empty()


@pytest.mark.parametrize(
    "raw",
    [b"\x80\xff\xfe", b"{not json"],
    ids=["invalid-utf8", "malformed-json"],
)
def test_tool_cache_load_discards_an_unreadable_file(
    tmp_path: Path,
    raw: bytes,
) -> None:
    """A cache that cannot be decoded is discarded instead of raising.

    Args:
        tmp_path: Pytest fixture for temporary directory.
        raw: Bytes written as the cache file.
    """
    (tmp_path / "t.json").write_bytes(raw)

    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        loaded = ToolCache.load("t")

    assert_that(loaded.fingerprints).is_empty()


def test_tool_cache_save_leaves_no_temp_file_when_writing_fails(
    tmp_path: Path,
) -> None:
    """A failed write removes its temp file and keeps the old cache.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    (tmp_path / "t.json").write_text("old")
    cache = ToolCache(tool_name="t")

    with (
        patch("lintro.utils.file_cache.CACHE_DIR", tmp_path),
        patch("lintro.utils.file_cache.json.dump", side_effect=TypeError("boom")),
    ):
        cache.save()

    assert_that(sorted(p.name for p in tmp_path.iterdir())).is_equal_to(["t.json"])
    assert_that((tmp_path / "t.json").read_text()).is_equal_to("old")


def test_tool_cache_save_writes_the_current_format(tmp_path: Path) -> None:
    """A saved cache carries the format version that load accepts.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        ToolCache(tool_name="t").save()

    data = json.loads((tmp_path / "t.json").read_text())
    assert_that(data["format_version"]).is_equal_to(CACHE_FORMAT_VERSION)


def test_tool_cache_clear_removes_all_fingerprints() -> None:
    """Clear removes all fingerprints."""
    cache = ToolCache(tool_name="test")
    cache.fingerprints["file1.py"] = FileFingerprint(
        path="file1.py",
        mtime=1234567890.0,
        size=100,
    )
    cache.clear()
    assert_that(cache.fingerprints).is_empty()


def test_tool_cache_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Save and load preserves cache data.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        cache = ToolCache(tool_name="test_tool")
        cache.fingerprints["/test/file.py"] = FileFingerprint(
            path="/test/file.py",
            mtime=1234567890.0,
            size=1024,
        )
        cache.save()

        loaded = ToolCache.load("test_tool")
        assert_that(loaded.tool_name).is_equal_to("test_tool")
        assert_that(loaded.fingerprints).contains_key("/test/file.py")


def test_tool_cache_load_returns_empty_for_missing_file(tmp_path: Path) -> None:
    """Load returns empty cache for missing cache file.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        loaded = ToolCache.load("nonexistent_tool")
        assert_that(loaded.fingerprints).is_empty()


def test_clear_all_caches_deletes_files(tmp_path: Path) -> None:
    """Clear all caches deletes all cache files.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    (tmp_path / "tool1.json").write_text('{"tool_name": "tool1"}')
    (tmp_path / "tool2.json").write_text('{"tool_name": "tool2"}')

    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        clear_all_caches()
        assert_that(list(tmp_path.glob("*.json"))).is_empty()


def test_get_cache_stats_returns_file_counts(tmp_path: Path) -> None:
    """Get cache stats returns file counts.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    cache_data = {
        "tool_name": "test_tool",
        "fingerprints": {
            "/file1.py": {"path": "/file1.py", "mtime": 1.0, "size": 100},
            "/file2.py": {"path": "/file2.py", "mtime": 2.0, "size": 200},
        },
    }
    (tmp_path / "test_tool.json").write_text(json.dumps(cache_data))

    with patch("lintro.utils.file_cache.CACHE_DIR", tmp_path):
        stats = get_cache_stats()
        assert_that(stats).contains_key("test_tool")
        assert_that(stats["test_tool"]).is_equal_to(2)


def test_get_cache_stats_returns_empty_for_nonexistent_dir(tmp_path: Path) -> None:
    """Get cache stats returns empty dict for nonexistent directory.

    Args:
        tmp_path: Pytest fixture for temporary directory.
    """
    nonexistent_dir = tmp_path / "nonexistent"

    with patch("lintro.utils.file_cache.CACHE_DIR", nonexistent_dir):
        stats = get_cache_stats()
        assert_that(stats).is_empty()
