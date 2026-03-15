"""Tests for the AI suggestion cache."""

from __future__ import annotations

import json
import time

from assertpy import assert_that

from lintro.ai.cache import (
    CACHE_DIR,
    _cache_key,
    _evict_lru,
    cache_suggestion,
    get_cached_suggestion,
)
from lintro.ai.models import AIFixSuggestion


def _make_suggestion(**kwargs: object) -> AIFixSuggestion:
    """Create a minimal AIFixSuggestion for tests."""
    defaults = {
        "file": "test.py",
        "line": 1,
        "code": "E001",
        "original_code": "x",
        "suggested_code": "y",
    }
    defaults.update(kwargs)
    return AIFixSuggestion(**defaults)  # type: ignore[arg-type]


# -- _cache_key --------------------------------------------------------------


def test_cache_key_is_deterministic() -> None:
    """Same inputs always produce the same key."""
    key1 = _cache_key("content", "E001", 10, "msg")
    key2 = _cache_key("content", "E001", 10, "msg")
    assert_that(key1).is_equal_to(key2)


def test_cache_key_differs_for_different_inputs() -> None:
    """Different inputs produce different keys."""
    key1 = _cache_key("content", "E001", 10, "msg")
    key2 = _cache_key("content", "E002", 10, "msg")
    assert_that(key1).is_not_equal_to(key2)


def test_cache_key_is_16_hex_chars() -> None:
    """Cache key is a 16-character hex string."""
    key = _cache_key("content", "E001", 10, "msg")
    assert_that(key).matches(r"^[0-9a-f]{16}$")


# -- get_cached_suggestion ---------------------------------------------------


def test_cache_miss_returns_none(tmp_path: object) -> None:
    """A cache miss returns None."""
    result = get_cached_suggestion(tmp_path, "content", "E001", 10, "msg")  # type: ignore[arg-type]
    assert_that(result).is_none()


def test_cache_hit_returns_data(tmp_path: object) -> None:
    """A cache hit returns the stored suggestion as AIFixSuggestion."""
    from pathlib import Path

    root = Path(str(tmp_path))
    suggestion = _make_suggestion()
    cache_suggestion(root, "content", "E001", 10, "msg", suggestion)

    result = get_cached_suggestion(root, "content", "E001", 10, "msg")
    assert_that(result).is_not_none()
    assert_that(result).is_instance_of(AIFixSuggestion)
    assert_that(result.file).is_equal_to("test.py")
    assert_that(result.original_code).is_equal_to("x")
    assert_that(result.suggested_code).is_equal_to("y")


def test_expired_cache_returns_none_and_deletes_file(tmp_path: object) -> None:
    """An expired cache entry returns None and removes the file."""
    from pathlib import Path

    root = Path(str(tmp_path))
    suggestion = {"file": "test.py", "line": 1, "code": "E001"}

    # Write a cache entry with a timestamp far in the past
    key = _cache_key("content", "E001", 10, "msg")
    cache_dir = root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{key}.json"
    cache_file.write_text(
        json.dumps({"timestamp": time.time() - 7200, "suggestion": suggestion}),
    )

    result = get_cached_suggestion(root, "content", "E001", 10, "msg", ttl=3600)
    assert_that(result).is_none()
    assert_that(cache_file.exists()).is_false()


# -- cache_suggestion --------------------------------------------------------


def test_cache_stores_to_correct_path(tmp_path: object) -> None:
    """cache_suggestion writes a JSON file under CACHE_DIR."""
    from pathlib import Path

    root = Path(str(tmp_path))
    suggestion = _make_suggestion()
    cache_suggestion(root, "content", "E001", 10, "msg", suggestion)

    key = _cache_key("content", "E001", 10, "msg")
    cache_file = root / CACHE_DIR / f"{key}.json"
    assert_that(cache_file.exists()).is_true()

    data = json.loads(cache_file.read_text())
    assert_that(data).contains_key("timestamp", "suggestion")
    assert_that(data["suggestion"]["file"]).is_equal_to("test.py")


# -- LRU eviction -------------------------------------------------------------


def test_evict_lru_removes_oldest_entries(tmp_path: object) -> None:
    """_evict_lru removes the least recently modified entries."""
    import os
    from pathlib import Path

    cache_dir = Path(str(tmp_path)) / "cache"
    cache_dir.mkdir()

    # Create 5 files with staggered mtime values
    for i in range(5):
        f = cache_dir / f"entry_{i}.json"
        f.write_text(json.dumps({"i": i}))
        # Set mtime so entry_0 is oldest, entry_4 is newest
        os.utime(f, (1000 + i, 1000 + i))

    _evict_lru(cache_dir, max_entries=3)

    remaining = sorted(p.name for p in cache_dir.glob("*.json"))
    assert_that(remaining).is_equal_to(["entry_2.json", "entry_3.json", "entry_4.json"])


def test_evict_lru_no_op_when_under_limit(tmp_path: object) -> None:
    """_evict_lru does nothing when entry count is within the limit."""
    from pathlib import Path

    cache_dir = Path(str(tmp_path)) / "cache"
    cache_dir.mkdir()

    for i in range(3):
        (cache_dir / f"entry_{i}.json").write_text("{}")

    _evict_lru(cache_dir, max_entries=5)

    remaining = list(cache_dir.glob("*.json"))
    assert_that(remaining).is_length(3)


def test_cache_suggestion_evicts_when_over_max(tmp_path: object) -> None:
    """cache_suggestion triggers LRU eviction when max_entries is exceeded."""
    import os
    from pathlib import Path

    root = Path(str(tmp_path))
    max_entries = 3

    # Pre-populate cache with entries that have known mtime ordering
    cache_dir = root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        f = cache_dir / f"old_entry_{i}.json"
        f.write_text(json.dumps({"timestamp": time.time(), "suggestion": {"i": i}}))
        # Make entries progressively older
        os.utime(f, (1000 + i, 1000 + i))

    # Adding one more entry should evict the oldest (old_entry_0)
    suggestion = _make_suggestion(code="E999")
    cache_suggestion(
        root, "new", "E999", 1, "new msg", suggestion, max_entries=max_entries,
    )

    remaining = sorted(p.name for p in cache_dir.glob("*.json"))
    # old_entry_0 (mtime=1000) should have been evicted
    assert_that(remaining).does_not_contain("old_entry_0.json")
    # Should have exactly max_entries files
    assert_that(remaining).is_length(max_entries)


def test_get_cached_suggestion_updates_mtime(tmp_path: object) -> None:
    """Accessing a cached entry touches the file so it becomes most recent."""
    import os
    from pathlib import Path

    root = Path(str(tmp_path))
    suggestion = _make_suggestion()
    cache_suggestion(root, "content", "E001", 10, "msg", suggestion)

    key = _cache_key("content", "E001", 10, "msg")
    cache_file = root / CACHE_DIR / f"{key}.json"

    # Set mtime to the past
    os.utime(cache_file, (1000, 1000))
    old_mtime = cache_file.stat().st_mtime

    # Access the entry
    result = get_cached_suggestion(root, "content", "E001", 10, "msg")
    assert_that(result).is_not_none()

    new_mtime = cache_file.stat().st_mtime
    assert_that(new_mtime).is_greater_than(old_mtime)
