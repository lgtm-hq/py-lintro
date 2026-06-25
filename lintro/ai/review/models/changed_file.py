"""Changed file metadata for review diffs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChangedFile:
    """A file changed in the review diff.

    Attributes:
        path: Repository-relative file path.
        status: Change status (added, modified, deleted, renamed).
        additions: Number of added lines.
        deletions: Number of deleted lines.
    """

    path: str
    status: str
    additions: int
    deletions: int
