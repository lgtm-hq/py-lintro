"""Shared disclosure for a context selection that has no reviewable changes."""

from __future__ import annotations

from typing import Any

from lintro.ai.review.exceptions import ReviewContextError

NO_CHANGES_DETAIL = "No reviewable changes, no review ran."


def no_changes_payload(error: ReviewContextError) -> dict[str, Any]:
    """Build an explicit skip envelope without claiming review coverage.

    Args:
        error: The empty context selection, retaining deliberate exclusions.

    Returns:
        A JSON-compatible disclosure of the skipped review and files.
    """
    return {
        "outcome": "no_changes",
        "review_ran": False,
        "detail": NO_CHANGES_DETAIL,
        "skipped_files": [
            {"path": item.path, "reason": item.reason.value, "detail": item.label}
            for item in error.skipped_files
        ],
    }
