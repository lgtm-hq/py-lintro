"""Path canonicalisation shared by ruff's check and fix flows.

``ruff format --check`` prints paths as it was given them, so they are
relative whenever the command ran from a working directory. Both flows have to
turn those into absolute paths, and they have to do it *identically*: the
run-level verify pass (#1743) matches a fix's ``initial_issues`` against the
files it fingerprinted and against the residual a later check reports, and any
divergence between the two would key the same file under two different names.
"""

from __future__ import annotations

import os

__all__ = ["absolute_issue_paths"]


def absolute_issue_paths(*, files: list[str], cwd: str | None) -> list[str]:
    """Resolve ruff's reported paths against the directory it ran in.

    Args:
        files: Paths exactly as ruff printed them.
        cwd: Working directory the command ran in, or ``None`` when it ran in
            the process directory and ruff's paths can be used as-is.

    Returns:
        The same paths, made absolute where a working directory is known.
        Paths that are already absolute are returned unchanged.
    """
    resolved: list[str] = []
    for file_path in files:
        if cwd and not os.path.isabs(file_path):
            resolved.append(os.path.abspath(os.path.join(cwd, file_path)))
        else:
            resolved.append(file_path)
    return resolved
