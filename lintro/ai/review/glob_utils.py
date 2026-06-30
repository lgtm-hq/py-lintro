"""Shared glob matching for AI diff review path classification."""

from __future__ import annotations

import fnmatch
import re

__all__ = [
    "expand_glob_pattern",
    "normalize_path",
    "path_matches_any_glob",
    "path_matches_glob",
]

_BRACE_GROUP_PATTERN = re.compile(r"\{([^{}]+)\}")
_SEGMENT_GLOB_PATTERN = re.compile(r"\*\*/([^*?[\]/{}]+)/\*\*$")


def normalize_path(*, path: str) -> str:
    """Normalize a repository path for glob matching.

    Args:
        path: Raw repository-relative path.

    Returns:
        Forward-slash normalized path.
    """
    return path.replace("\\", "/").removeprefix("./")


def expand_glob_pattern(*, pattern: str) -> list[str]:
    """Expand brace groups in a glob pattern.

    Args:
        pattern: Glob pattern that may contain ``{a,b}`` groups.

    Returns:
        One or more concrete glob patterns.
    """
    match = _BRACE_GROUP_PATTERN.search(pattern)
    if match is None:
        return [pattern]

    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    alternatives = match.group(1).split(",")
    expanded: list[str] = []
    for alternative in alternatives:
        expanded.extend(
            expand_glob_pattern(pattern=f"{prefix}{alternative}{suffix}"),
        )
    return expanded


def _matches_root_anchored_pattern(*, normalized: str, expanded: str) -> bool:
    """Match repository-root globs without suffix false positives.

    Patterns like ``scripts/**`` or ``pyproject.toml`` must not match nested
    paths such as ``vendor/scripts/run.sh`` or ``packages/pyproject.toml``.
    """
    if expanded.startswith("**/"):
        return False
    if "**" in expanded:
        return _double_star_segments_match(path=normalized, pattern=expanded)
    if "/" in expanded:
        return _segments_match(path=normalized, pattern=expanded)
    if "/" in normalized:
        return False
    return fnmatch.fnmatchcase(normalized, expanded)


def _segments_match(*, path: str, pattern: str) -> bool:
    """Match slash-containing root patterns without crossing path segments."""
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")
    if len(path_parts) != len(pattern_parts):
        return False
    return all(
        fnmatch.fnmatchcase(path_part, pattern_part)
        for path_part, pattern_part in zip(path_parts, pattern_parts, strict=True)
    )


def _double_star_segments_match(*, path: str, pattern: str) -> bool:
    """Match root-anchored patterns that contain an internal ``**`` segment."""

    def match_at(*, path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        pattern_part = pattern_parts[pattern_index]
        if pattern_part == "**":
            if pattern_index == len(pattern_parts) - 1:
                return True
            for skip in range(len(path_parts) - path_index + 1):
                if match_at(
                    path_index=path_index + skip,
                    pattern_index=pattern_index + 1,
                ):
                    return True
            return False
        if path_index >= len(path_parts):
            return False
        if not fnmatch.fnmatchcase(path_parts[path_index], pattern_part):
            return False
        return match_at(path_index=path_index + 1, pattern_index=pattern_index + 1)

    path_parts = path.split("/")
    pattern_parts = pattern.split("/")
    return match_at(path_index=0, pattern_index=0)


def path_matches_glob(*, path: str, pattern: str) -> bool:
    """Match a repository path against a glob pattern.

    Args:
        path: Normalized repository-relative file path.
        pattern: Glob pattern, optionally with brace expansion.

    Returns:
        True when the path matches the pattern.
    """
    normalized = normalize_path(path=path)
    for expanded in expand_glob_pattern(pattern=pattern):
        if _matches_root_anchored_pattern(normalized=normalized, expanded=expanded):
            return True

        if expanded.endswith("/**"):
            segment_match = _SEGMENT_GLOB_PATTERN.fullmatch(expanded)
            if segment_match is not None:
                segment = segment_match.group(1)
                parts = normalized.split("/")
                for index, part in enumerate(parts):
                    if part == segment and index < len(parts) - 1:
                        return True
                continue
        if "**" in expanded:
            if _double_star_segments_match(path=normalized, pattern=expanded):
                return True
            continue
    return False


def path_matches_any_glob(*, path: str, patterns: tuple[str, ...]) -> bool:
    """Return True when a path matches any expanded glob pattern."""
    normalized = normalize_path(path=path)
    return any(
        path_matches_glob(path=normalized, pattern=pattern) for pattern in patterns
    )
