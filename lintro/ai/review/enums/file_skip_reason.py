"""Reasons a changed file was excluded from an AI review run."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["FILE_SKIP_REASON_LABELS", "FileSkipReason", "describe_skip_reason"]


class FileSkipReason(StrEnum):
    """Why the file-selection step dropped a changed file.

    A count of skipped files is not actionable on its own: "2 skipped" reads
    as either a deliberate filter or a silent gap in coverage. Recording the
    reason at the point of exclusion is what lets the per-review comment say
    which of the two it was (#1910).
    """

    PATH_FILTER = auto()
    REPETITIVE_DIFF = auto()


#: Human-readable phrase rendered after a skipped file's path.
FILE_SKIP_REASON_LABELS: dict[FileSkipReason, str] = {
    FileSkipReason.PATH_FILTER: "outside the requested --path filter",
    FileSkipReason.REPETITIVE_DIFF: (
        "identical to a sampled file's diff (repetitive-diff sampling)"
    ),
}

_MISSING_LABELS = set(FileSkipReason) - set(FILE_SKIP_REASON_LABELS)
if _MISSING_LABELS:  # pragma: no cover - guards a future skip reason
    raise RuntimeError(
        f"FileSkipReason members without a label: {_MISSING_LABELS}",
    )


def describe_skip_reason(*, reason: FileSkipReason) -> str:
    """Return the human-readable phrase for a skip reason.

    Args:
        reason: Skip reason recorded by the file-selection step.

    Returns:
        The phrase rendered after the file path in review surfaces.
    """
    return FILE_SKIP_REASON_LABELS[reason]
