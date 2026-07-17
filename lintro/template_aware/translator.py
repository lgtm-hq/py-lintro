"""Issue translator: rewrite host-linter issues onto original template paths."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from typing import TypeVar

from lintro.parsers.base_issue import BaseIssue
from lintro.template_aware.source_map import SourceMap

IssueT = TypeVar("IssueT", bound=BaseIssue)


def _normalize_path(path: str) -> str:
    """Normalize a path for map lookup.

    Args:
        path: Absolute or relative file path.

    Returns:
        Resolved absolute path when possible, otherwise the original string.
    """
    if not path:
        return path
    try:
        return os.path.abspath(path)
    except OSError:
        return path


def translate_issue(
    issue: IssueT,
    source_maps: dict[str, SourceMap],
) -> IssueT:
    """Rewrite one issue's file/line onto the original template coordinates.

    Args:
        issue: Parsed host-linter issue (mutated fields via ``dataclasses.replace``).
        source_maps: Mapping of rendered absolute path → SourceMap.

    Returns:
        New issue instance with remapped ``file`` / ``line`` (and ``end_line``
        when present). Unmapped issues are returned unchanged.
    """
    issue_path = _normalize_path(issue.file)
    source_map = source_maps.get(issue_path)
    if source_map is None:
        # Also try basename-only match when tools emit relative paths.
        for rendered_path, candidate in source_maps.items():
            if os.path.basename(rendered_path) == os.path.basename(issue.file):
                source_map = candidate
                break
    if source_map is None:
        return issue

    new_line = source_map.lookup_line(issue.line)
    updated = replace(
        issue,
        file=source_map.original_path,
        line=new_line,
    )

    if hasattr(updated, "end_line"):
        end_line = getattr(updated, "end_line", 0) or 0
        if isinstance(end_line, int) and end_line > 0:
            # end_line is optional on BaseIssue subclasses; setattr avoids
            # typed replace() rejecting a field absent from IssueT.
            object.__setattr__(
                updated,
                "end_line",
                source_map.lookup_line(end_line),
            )

    return updated


def translate_issues(
    issues: Sequence[IssueT],
    source_maps: dict[str, SourceMap],
) -> list[IssueT]:
    """Translate a sequence of issues back to original template coordinates.

    Args:
        issues: Host-linter issues.
        source_maps: Mapping of rendered absolute path → SourceMap.

    Returns:
        List of translated issues (same length/order as input).
    """
    if not source_maps or not issues:
        return list(issues)
    return [translate_issue(issue=issue, source_maps=source_maps) for issue in issues]
