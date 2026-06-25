"""Checklist item selection and prompt formatting."""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from lintro.ai.review.models.checklist_item import ChecklistItem

__all__ = [
    "format_checklist_for_prompt",
    "select_checklist_items",
]

_BRACE_GROUP_PATTERN = re.compile(r"\{([^{}]+)\}")


def select_checklist_items(
    *,
    changed_files: list[str],
    items: list[ChecklistItem],
) -> list[ChecklistItem]:
    """Select checklist items for the changed files in a review diff.

    Tier 1 items (empty triggers) are always included. Tier 2 and custom items
    are included when any changed file matches any trigger glob.

    Args:
        changed_files: Repository-relative paths changed in the diff.
        items: Full checklist registry (builtin plus custom config items).

    Returns:
        Selected items sorted by stable checklist id.
    """
    normalized_files = [_normalize_path(path=path) for path in changed_files]
    selected: list[ChecklistItem] = []

    for item in items:
        if item.tier == 1 or not item.triggers:
            selected.append(item)
            continue
        if _item_matches_files(
            triggers=item.triggers,
            changed_files=normalized_files,
        ):
            selected.append(item)

    return sorted(selected, key=lambda checklist_item: checklist_item.id)


def format_checklist_for_prompt(
    *,
    items: list[ChecklistItem],
) -> tuple[str, dict[int, int]]:
    """Format selected checklist items for the review prompt.

    Renumbers items sequentially (1..N) in prompt order while preserving the
    mapping back to stable checklist ids for finding attribution.

    Args:
        items: Selected checklist items sorted by id.

    Returns:
        Tuple of formatted prompt text and prompt-id to checklist-id mapping.
    """
    lines: list[str] = []
    prompt_to_checklist: dict[int, int] = {}

    for prompt_id, item in enumerate(items, start=1):
        prompt_to_checklist[prompt_id] = item.id
        lines.append(
            f"{prompt_id}. [{item.category.value}] {item.question}",
        )

    return "\n".join(lines), prompt_to_checklist


def _item_matches_files(
    *,
    triggers: list[str],
    changed_files: list[str],
) -> bool:
    """Return True when any changed file matches any trigger pattern.

    Args:
        triggers: Glob patterns that activate a checklist item.
        changed_files: Normalized repository-relative file paths.

    Returns:
        True when at least one trigger matches at least one changed file.
    """
    for trigger in triggers:
        for pattern in _expand_glob_pattern(pattern=trigger):
            for changed_file in changed_files:
                if _path_matches_pattern(path=changed_file, pattern=pattern):
                    return True
    return False


def _path_matches_pattern(*, path: str, pattern: str) -> bool:
    """Match a repository path against a glob pattern.

    Args:
        path: Normalized repository-relative file path.
        pattern: Glob pattern, optionally with brace expansion.

    Returns:
        True when the path matches the pattern.
    """
    pure_path = PurePosixPath(path)
    if pure_path.match(pattern):
        return True
    if fnmatch.fnmatch(path, pattern):
        return True
    # PurePosixPath.match() and fnmatch do not match root-level files against
    # ``**/`` patterns (e.g. ``views.py`` vs ``**/views.py``). Prepend a
    # synthetic parent so root files participate in the same glob semantics.
    if "/" not in path and pattern.startswith("**/"):
        rooted_path = f"_root/{path}"
        rooted_pattern = pattern.replace("**/", "_root/", 1)
        if PurePosixPath(rooted_path).match(rooted_pattern):
            return True
        if fnmatch.fnmatch(rooted_path, rooted_pattern):
            return True
    return False


def _expand_glob_pattern(*, pattern: str) -> list[str]:
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
            _expand_glob_pattern(pattern=f"{prefix}{alternative}{suffix}"),
        )
    return expanded


def _normalize_path(*, path: str) -> str:
    """Normalize a repository path for glob matching.

    Args:
        path: Raw repository-relative path.

    Returns:
        Forward-slash normalized path.
    """
    return path.replace("\\", "/").removeprefix("./")
