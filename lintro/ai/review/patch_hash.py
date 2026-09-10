"""Normalized patch hashes for file-level review coverage (#2154).

Coverage identity is ``(path, hash)``. The hash covers only added and
removed lines so a content-identical rebase or update-branch keeps
coverage. Context lines and hunk headers are ignored on purpose: they
measure surrounding text, not the change. Contextual drift is the
invalidation graph's job (ADR-0007).
"""

from __future__ import annotations

import hashlib

__all__ = ["normalized_patch_hash"]


def normalized_patch_hash(diff_text: str) -> str:
    """Hash the added and removed lines of a unified per-file diff.

    Args:
        diff_text: Unified diff for one file (headers optional).

    Returns:
        Hex sha256 of the ``+``/``-`` payload. An empty payload (pure
        context, file mode, or rename without body) hashes the empty
        string so two empty changes still compare equal.
    """
    lines: list[str] = []
    for raw in diff_text.splitlines():
        if raw.startswith(
            (
                "+++",
                "---",
                "@@",
                "diff ",
                "index ",
                "new file",
                "deleted file",
            ),
        ):
            continue
        if raw.startswith(("+", "-")):
            lines.append(raw)
    payload = "\n".join(lines)
    return hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()
