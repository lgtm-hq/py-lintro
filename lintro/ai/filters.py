"""Issue filtering for AI processing."""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.parsers.base_issue import BaseIssue


def _glob_match(path: str, pattern: str) -> bool:
    """Match a path against a glob pattern with recursive ``**`` support.

    Uses ``PurePosixPath.full_match`` (Python 3.13+) when available,
    falling back to a regex-based converter for older versions.

    ``**`` matches zero or more path segments (including separators).
    ``*`` matches any characters except ``/``.
    ``?`` matches any single character except ``/``.

    Args:
        path: File path to test (forward-slash separated).
        pattern: Glob pattern, may contain ``**`` for recursive matching.

    Returns:
        True if the path matches the pattern.
    """
    p = PurePosixPath(path)
    if hasattr(p, "full_match"):
        return bool(p.full_match(pattern))

    # Fallback for Python <3.13: convert glob to regex
    i = 0
    n = len(pattern)
    regex = "^"
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** — match zero or more path segments
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    regex += "(?:.+/)?"
                else:
                    regex += ".*"
                continue
            else:
                regex += "[^/]*"
        elif c == "?":
            regex += "[^/]"
        else:
            regex += re.escape(c)
        i += 1
    regex += "$"
    return bool(re.match(regex, path))


def should_process_issue(issue: BaseIssue, config: AIConfig) -> bool:
    """Check if an issue should be sent to AI based on path/rule filters.

    Evaluates include/exclude glob patterns for both file paths and rule
    codes. Include patterns act as an allowlist (only matching items are
    processed). Exclude patterns act as a denylist (matching items are
    skipped). When both include and exclude are set, include is checked
    first.

    Path patterns support recursive ``**`` matching (e.g. ``src/**/*.py``).
    Rule patterns use standard ``fnmatch`` (e.g. ``E5*``, ``F401``).

    Args:
        issue: The issue to evaluate.
        config: AI configuration containing filter patterns.

    Returns:
        True if the issue should be processed by AI, False otherwise.
    """
    file_path = getattr(issue, "file", "") or ""
    code = getattr(issue, "code", "") or ""

    # Path filtering — use _glob_match for ** patterns, fnmatch otherwise
    if config.include_paths and not any(
        _glob_match(file_path, p) if "**" in p else fnmatch.fnmatch(file_path, p)
        for p in config.include_paths
    ):
        return False
    if config.exclude_paths and any(
        _glob_match(file_path, p) if "**" in p else fnmatch.fnmatch(file_path, p)
        for p in config.exclude_paths
    ):
        return False

    # Rule filtering (fnmatch is fine for error codes)
    if config.include_rules and not any(
        fnmatch.fnmatch(code, r) for r in config.include_rules
    ):
        return False
    return not (
        config.exclude_rules
        and any(fnmatch.fnmatch(code, r) for r in config.exclude_rules)
    )


def filter_issues(issues: list[BaseIssue], config: AIConfig) -> list[BaseIssue]:
    """Filter a list of issues based on AI path/rule configuration.

    Args:
        issues: List of issues to filter.
        config: AI configuration containing filter patterns.

    Returns:
        Filtered list containing only issues that pass all filters.
    """
    return [i for i in issues if should_process_issue(i, config)]
