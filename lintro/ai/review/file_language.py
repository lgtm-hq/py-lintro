"""Language tagging for changed files via the ``identify`` library.

The checklist language axis reuses ``identify``'s maintained filename-to-tag
mapping instead of a hand-maintained extension table. Paths under ``bin/`` or
``scripts/`` also receive a ``shell`` tag, and extensionless scripts can be
resolved from shebangs when a repository root is available.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from identify import identify

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "languages_for_path",
    "languages_for_paths",
]

_EXTENSIONLESS_SCRIPT_ROOTS = frozenset({"bin", "scripts"})


def _is_script_path(*, normalized: str) -> bool:
    """Return True when a path sits under a conventional script directory."""
    pure_path = PurePosixPath(normalized)
    if pure_path.parts and pure_path.parts[0] in _EXTENSIONLESS_SCRIPT_ROOTS:
        return True
    return "scripts" in pure_path.parts or "bin" in pure_path.parts


def languages_for_path(
    *,
    path: str,
    repo_root: Path | str | None = None,
) -> set[str]:
    """Return ``identify`` tags for a single repository-relative path.

    Args:
        path: Repository-relative file path.
        repo_root: Optional repository root used to resolve extensionless script
            shebangs under ``bin/`` or ``scripts/``.

    Returns:
        Set of ``identify`` tags (for example ``{"rust", "text"}``) derived from
        the path basename. Script paths also receive a ``shell`` tag. When
        ``repo_root`` is set, extensionless scripts may gain interpreter tags
        from their shebang.
    """
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    tags = set(identify.tags_from_filename(name))

    if _is_script_path(normalized=normalized):
        tags.add("shell")
        if repo_root is not None:
            full_path = Path(repo_root) / normalized
            if full_path.is_file():
                tags |= set(identify.tags_from_path(str(full_path)))
        return tags

    return tags


def languages_for_paths(
    *,
    paths: Iterable[str],
    repo_root: Path | str | None = None,
) -> set[str]:
    """Return the union of ``identify`` tags across many paths.

    Args:
        paths: Repository-relative file paths from the review diff.
        repo_root: Optional repository root passed through to
            :func:`languages_for_path`.

    Returns:
        Combined set of ``identify`` tags present across all paths.
    """
    tags: set[str] = set()
    for path in paths:
        tags |= languages_for_path(path=path, repo_root=repo_root)
    return tags
