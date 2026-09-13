"""File fingerprint caching for incremental checks.

This module provides functionality to cache file metadata (mtime, size) to enable
incremental linting - only checking files that have changed since the last run.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

# Cache directory location
CACHE_DIR = Path.home() / ".lintro" / "cache"


@dataclass
class FileFingerprint:
    """Fingerprint of a file for change detection.

    Attributes:
        path: Absolute path to the file.
        mtime: Last modification time (seconds since epoch).
        size: File size in bytes.
    """

    path: str
    mtime: float
    size: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the fingerprint.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileFingerprint:
        """Create from dictionary.

        Args:
            data: Dictionary with path, mtime, and size keys.

        Returns:
            FileFingerprint instance created from the dictionary.
        """
        return cls(
            path=data["path"],
            mtime=data["mtime"],
            size=data["size"],
        )


@dataclass
class ToolCache:
    """Cache of file fingerprints for a specific tool.

    Attributes:
        tool_name: Name of the tool this cache is for.
        fingerprints: Dictionary mapping file paths to their fingerprints.
    """

    tool_name: str
    fingerprints: dict[str, FileFingerprint] = field(default_factory=dict)

    def get_changed_files(self, files: list[str]) -> list[str]:
        """Return only files that have changed since last run.

        A file is considered changed if:
        - It's new (not in cache)
        - Its mtime has changed
        - Its size has changed

        Args:
            files: List of absolute file paths to check.

        Returns:
            List of file paths that have changed.
        """
        changed: list[str] = []

        for file_path in files:
            path = Path(file_path)
            if not path.exists():
                continue

            try:
                stat = path.stat()
            except OSError as e:
                logger.debug(f"Could not stat {file_path}: {e}")
                changed.append(file_path)
                continue

            cached = self.fingerprints.get(file_path)

            if cached is None:
                # New file not in cache
                changed.append(file_path)
            elif cached.mtime != stat.st_mtime or cached.size != stat.st_size:
                # File has been modified
                changed.append(file_path)
            # else: file unchanged, skip it

        return changed

    def update(self, files: list[str]) -> None:
        """Update cache with current file states.

        Args:
            files: List of file paths to update in cache.
        """
        for file_path in files:
            path = Path(file_path)
            if not path.exists():
                # Remove from cache if file no longer exists
                self.fingerprints.pop(file_path, None)
                continue

            try:
                stat = path.stat()
                self.fingerprints[file_path] = FileFingerprint(
                    path=file_path,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                )
            except OSError as e:
                logger.debug(f"Could not update cache for {file_path}: {e}")

    def save(self) -> None:
        """Persist cache to disk using atomic write.

        Uses temp file + rename pattern to prevent corruption if write fails.
        """
        cache_file = CACHE_DIR / f"{self.tool_name}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                "tool_name": self.tool_name,
                "fingerprints": {
                    path: fp.to_dict() for path, fp in self.fingerprints.items()
                },
            }
            # Write to temp file first, then atomically rename
            # This prevents corruption if the write is interrupted
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cache_file.parent,
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                json.dump(data, tmp_file, indent=2)
                tmp_path = Path(tmp_file.name)

            # Atomic rename (on POSIX systems)
            tmp_path.replace(cache_file)

            logger.debug(
                f"Saved cache for {self.tool_name} ({len(self.fingerprints)} files)",
            )
        except (OSError, TypeError, ValueError) as e:
            logger.warning(f"Could not save cache for {self.tool_name}: {e}")
            # Clean up temp file if it exists
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, tool_name: str) -> ToolCache:
        """Load cache from disk.

        Args:
            tool_name: Name of the tool to load cache for.

        Returns:
            Loaded cache, or empty cache if file doesn't exist.
        """
        cache_file = CACHE_DIR / f"{tool_name}.json"

        if not cache_file.exists():
            return cls(tool_name=tool_name)

        try:
            with cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)

            fingerprints = {
                path: FileFingerprint.from_dict(fp_data)
                for path, fp_data in data.get("fingerprints", {}).items()
            }

            cache = cls(tool_name=tool_name, fingerprints=fingerprints)
            logger.debug(f"Loaded cache for {tool_name} ({len(fingerprints)} files)")
            return cache
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Could not load cache for {tool_name}: {e}")
            return cls(tool_name=tool_name)

    def clear(self) -> None:
        """Clear all cached fingerprints."""
        self.fingerprints.clear()
        logger.debug(f"Cleared cache for {self.tool_name}")


def clear_all_caches() -> None:
    """Clear all tool caches."""
    if CACHE_DIR.exists():
        for cache_file in CACHE_DIR.glob("*.json"):
            try:
                cache_file.unlink()
                logger.debug(f"Deleted cache file: {cache_file}")
            except OSError as e:
                logger.warning(f"Could not delete {cache_file}: {e}")
        logger.info("Cleared all incremental check caches")
    else:
        logger.debug("No cache directory to clear")


def get_cache_stats() -> dict[str, int]:
    """Get statistics about cached files.

    Returns:
        Dictionary with tool names and their cached file counts.
    """
    stats: dict[str, int] = {}

    if not CACHE_DIR.exists():
        return stats

    for cache_file in CACHE_DIR.glob("*.json"):
        try:
            with cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            tool_name = data.get("tool_name", cache_file.stem)
            count = len(data.get("fingerprints", {}))
            stats[tool_name] = count
        except (OSError, json.JSONDecodeError):
            pass

    return stats


@dataclass(frozen=True)
class FingerprintSnapshot:
    """Fingerprints taken at one instant, with the paths that could not be read.

    Attributes:
        fingerprints: Fingerprint per absolute file path.
        unreadable: Paths whose ``stat`` failed. These are always treated as
            changed, because "we do not know" must degrade towards verifying
            more rather than less.
    """

    fingerprints: dict[str, FileFingerprint]
    unreadable: tuple[str, ...] = ()

    @property
    def is_reliable(self) -> bool:
        """Report whether the snapshot can narrow anything at all.

        Two things can make a snapshot unusable.

        A file that could not be stat'ed at all has no fingerprint to compare
        against, so nothing can be concluded about it, and "we do not know"
        must widen the scope rather than narrow it.

        mtime resolution is the subtler one: on a filesystem with whole-second
        granularity a formatter that rewrites a file inside the same second
        leaves the fingerprint unmoved, and the file would be skipped. Every
        sampled file must show a fractional ``st_mtime``. Requiring only
        *some* of them would miss the mixed case — a coarse bind mount
        alongside a sub-second local filesystem — where the coarse half is
        exactly the half that can hide a rewrite. A file whose mtime lands on
        an exact second by chance costs a fallback to the floor, which is a
        wasted check rather than a wrong answer.

        Returns:
            True when nothing was unreadable and every sampled mtime shows
            sub-second resolution. An empty sample is trivially reliable:
            there is nothing to narrow.
        """
        if self.unreadable:
            return False
        if not self.fingerprints:
            return True
        return all(fp.mtime % 1 for fp in self.fingerprints.values())

    def changed_paths(self) -> list[str]:
        """Re-stat every fingerprinted file and return the ones that moved.

        A file counts as moved when its mtime or its size differs from the
        snapshot, when it can no longer be stat'ed, or when it was already
        unreadable at snapshot time. Size alone is near-useless (a quote-style
        rewrite is byte-for-byte the same length) and serves only as a cheap
        tiebreak for a same-mtime rewrite.

        Returns:
            Sorted absolute paths whose fingerprint moved.
        """
        moved: set[str] = set(self.unreadable)
        for file_path, before in self.fingerprints.items():
            try:
                stat = Path(file_path).stat()
            except OSError as exc:
                logger.debug(f"Could not re-stat {file_path}: {exc}")
                moved.add(file_path)
                continue
            if before.mtime != stat.st_mtime or before.size != stat.st_size:
                moved.add(file_path)
        return sorted(moved)


def snapshot_fingerprints(files: Sequence[str]) -> FingerprintSnapshot:
    """Stat every file once so a later re-stat can tell which ones moved.

    Args:
        files: Absolute file paths to fingerprint.

    Returns:
        FingerprintSnapshot: The fingerprints that could be taken, plus the
        paths whose ``stat`` failed. Unreadable paths are carried separately
        rather than dropped so the caller can degrade to the floor for them.
    """
    fingerprints: dict[str, FileFingerprint] = {}
    unreadable: list[str] = []
    for file_path in files:
        try:
            stat = Path(file_path).stat()
        except OSError as exc:
            logger.debug(f"Could not fingerprint {file_path}: {exc}")
            unreadable.append(file_path)
            continue
        fingerprints[file_path] = FileFingerprint(
            path=file_path,
            mtime=stat.st_mtime,
            size=stat.st_size,
        )
    return FingerprintSnapshot(
        fingerprints=fingerprints,
        unreadable=tuple(sorted(unreadable)),
    )
