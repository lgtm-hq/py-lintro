"""Changed file metadata for review diffs."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus


@dataclass
class ChangedFile:
    """A file changed in the review diff.

    Attributes:
        path: Repository-relative file path.
        status: Change status (added, modified, deleted, renamed).
        additions: Number of added lines.
        deletions: Number of deleted lines.
        previous_path: Source path for renamed or copied files.
    """

    path: str
    status: ChangedFileStatus | str
    additions: int
    deletions: int
    previous_path: str | None = None

    def __post_init__(self) -> None:
        """Coerce string statuses and validate rename metadata."""
        if self.additions < 0 or self.deletions < 0:
            msg = "additions and deletions must be non-negative"
            raise ValueError(msg)
        if isinstance(self.status, str):
            self.status = ChangedFileStatus(self.status)
        elif not isinstance(self.status, ChangedFileStatus):
            msg = "status must be a ChangedFileStatus or valid status string"
            raise TypeError(msg)
        if (
            self.status
            in {
                ChangedFileStatus.RENAMED,
                ChangedFileStatus.COPIED,
            }
            and not self.previous_path
        ):
            msg = "previous_path is required for renamed or copied files"
            raise ValueError(msg)
        if self.previous_path and self.status not in {
            ChangedFileStatus.RENAMED,
            ChangedFileStatus.COPIED,
        }:
            msg = "previous_path is only allowed for renamed or copied files"
            raise ValueError(msg)
