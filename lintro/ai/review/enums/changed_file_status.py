"""Normalized change statuses for review diff metadata."""

from __future__ import annotations

from enum import StrEnum


class ChangedFileStatus(StrEnum):
    """Git-derived change status for a reviewed file."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type-changed"
