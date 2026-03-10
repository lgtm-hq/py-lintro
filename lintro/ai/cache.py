"""AI suggestion cache for deduplication across runs."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from lintro.ai.models import AIFixSuggestion

CACHE_DIR = ".lintro-cache/ai"
DEFAULT_TTL = 3600  # 1 hour
DEFAULT_MAX_ENTRIES = 1000


def _cache_key(
    file_content: str,
    issue_code: str,
    issue_line: int,
    issue_message: str,
) -> str:
    """Compute a short SHA-256 hash key for a suggestion lookup."""
    h = hashlib.sha256(
        f"{file_content}:{issue_code}:{issue_line}:{issue_message}".encode(),
    ).hexdigest()[:16]
    return h


def get_cached_suggestion(
    workspace_root: Path,
    file_content: str,
    issue_code: str,
    issue_line: int,
    issue_message: str,
    ttl: int = DEFAULT_TTL,
) -> AIFixSuggestion | None:
    """Return a cached suggestion if one exists and is not expired.

    Args:
        workspace_root: Project root directory.
        file_content: Full file content (used for cache key).
        issue_code: Linter error code.
        issue_line: 1-based line number.
        issue_message: Linter message text.
        ttl: Time-to-live in seconds.

    Returns:
        Cached AIFixSuggestion, or None if miss/expired.
    """
    key = _cache_key(file_content, issue_code, issue_line, issue_message)
    cache_file = workspace_root / CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    data = json.loads(cache_file.read_text())
    if time.time() - data.get("timestamp", 0) > ttl:
        cache_file.unlink(missing_ok=True)
        return None
    suggestion = data.get("suggestion")
    if isinstance(suggestion, dict):
        # Touch the file to update its access/modification time for LRU tracking
        cache_file.touch()
        return AIFixSuggestion(**{
            k: v
            for k, v in suggestion.items()
            if k in {f.name for f in dataclasses.fields(AIFixSuggestion)}
        })
    return None


def _evict_lru(cache_dir: Path, max_entries: int) -> None:
    """Evict least recently used entries when cache exceeds *max_entries*.

    Entries are sorted by file modification time; the oldest ones are
    removed until the number of entries is below *max_entries*.

    Args:
        cache_dir: Directory containing cache JSON files.
        max_entries: Maximum number of entries to retain.
    """
    entries = sorted(cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    to_remove = len(entries) - max_entries
    if to_remove <= 0:
        return
    for entry in entries[:to_remove]:
        entry.unlink(missing_ok=True)


def cache_suggestion(
    workspace_root: Path,
    file_content: str,
    issue_code: str,
    issue_line: int,
    issue_message: str,
    suggestion: AIFixSuggestion,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> None:
    """Persist a suggestion to the on-disk cache.

    When the cache directory exceeds *max_entries* files, least recently
    used entries (by file modification time) are evicted.

    Args:
        workspace_root: Project root directory.
        file_content: Full file content (used for cache key).
        issue_code: Linter error code.
        issue_line: 1-based line number.
        issue_message: Linter message text.
        suggestion: AIFixSuggestion to cache.
        max_entries: Maximum cache entries before LRU eviction.
    """
    key = _cache_key(file_content, issue_code, issue_line, issue_message)
    cache_dir = workspace_root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{key}.json"
    suggestion_data = dataclasses.asdict(suggestion)
    cache_file.write_text(
        json.dumps({"timestamp": time.time(), "suggestion": suggestion_data}),
    )
    _evict_lru(cache_dir, max_entries)
