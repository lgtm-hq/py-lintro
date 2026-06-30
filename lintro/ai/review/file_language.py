"""Language tagging for changed files via the ``identify`` library.

The checklist language axis reuses ``identify``'s maintained filename-to-tag
mapping instead of a hand-maintained extension table. Top-level ``bin/`` and
``scripts/`` paths may receive a ``shell`` tag when the file looks like an
actual script, and extensionless entries can be resolved from shebangs when a
repository root is available.
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

_SCRIPT_ROOTS = frozenset({"bin", "scripts"})
_SHELL_SUFFIXES = frozenset({".sh", ".bash", ".bats"})
_NON_SCRIPT_SUFFIXES = frozenset(
    {
        ".md",
        ".rst",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
    },
)


def _is_script_path(*, normalized: str) -> bool:
    """Return True when a path sits under a top-level script directory."""
    pure_path = PurePosixPath(normalized)
    return bool(pure_path.parts) and pure_path.parts[0] in _SCRIPT_ROOTS


def _is_non_script_artifact(*, normalized: str) -> bool:
    """Return True when a script-directory path is docs or config, not code."""
    pure_path = PurePosixPath(normalized)
    suffix = pure_path.suffix.lower()
    if suffix in _NON_SCRIPT_SUFFIXES:
        return True
    return suffix == "" and pure_path.stem.lower() == "readme"


def _should_add_shell_tag(*, normalized: str, tags: set[str]) -> bool:
    """Return True when a top-level script path should receive a shell tag."""
    pure_path = PurePosixPath(normalized)
    suffix = pure_path.suffix.lower()
    if suffix in _SHELL_SUFFIXES:
        return True
    if suffix == "":
        return True
    return "shell" in tags


def languages_for_path(
    *,
    path: str,
    repo_root: Path | str | None = None,
) -> set[str]:
    """Return ``identify`` tags for a single repository-relative path.

    Args:
        path: Repository-relative file path.
        repo_root: Optional repository root used to resolve extensionless script
            shebangs under top-level ``bin/`` or ``scripts/``.

    Returns:
        Set of ``identify`` tags (for example ``{"rust", "text"}``) derived from
        the path basename. Conventional script paths may also receive a ``shell``
        tag. When ``repo_root`` is set, extensionless scripts may gain
        interpreter tags from their shebang.
    """
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    tags = set(identify.tags_from_filename(name))

    if not _is_script_path(normalized=normalized):
        return tags
    if _is_non_script_artifact(normalized=normalized):
        return tags

    if repo_root is not None:
        full_path = Path(repo_root) / normalized
        if full_path.is_file():
            tags |= set(identify.tags_from_path(str(full_path)))

    if _should_add_shell_tag(normalized=normalized, tags=tags):
        tags.add("shell")

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
