"""Language tagging for changed files via the ``identify`` library.

The checklist language axis reuses ``identify``'s maintained filename-to-tag
mapping instead of a hand-maintained extension table. Tags are derived from the
path basename, with a small heuristic for extensionless scripts under ``bin/``
or ``scripts/``.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from identify import identify

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "languages_for_path",
    "languages_for_paths",
]

_EXTENSIONLESS_SCRIPT_ROOTS = frozenset({"bin", "scripts"})


def languages_for_path(*, path: str) -> set[str]:
    """Return ``identify`` tags for a single repository-relative path.

    Args:
        path: Repository-relative file path.

    Returns:
        Set of ``identify`` tags (for example ``{"rust", "text"}``) derived from
        the path basename. Extensionless paths under ``bin/`` or ``scripts/``
        receive a ``shell`` tag as a conservative script heuristic.
    """
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    tags = set(identify.tags_from_filename(name))
    if tags:
        return tags

    pure_path = PurePosixPath(normalized)
    if (
        pure_path.suffix == ""
        and pure_path.parts
        and (
            pure_path.parts[0] in _EXTENSIONLESS_SCRIPT_ROOTS
            or "scripts" in pure_path.parts
        )
    ):
        tags.add("shell")
    return tags


def languages_for_paths(*, paths: Iterable[str]) -> set[str]:
    """Return the union of ``identify`` tags across many paths.

    Args:
        paths: Repository-relative file paths from the review diff.

    Returns:
        Combined set of ``identify`` tags present across all paths.
    """
    tags: set[str] = set()
    for path in paths:
        tags |= languages_for_path(path=path)
    return tags
