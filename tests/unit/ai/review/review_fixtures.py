"""Helpers for loading AI review diff fixtures from test_samples."""

from __future__ import annotations

from pathlib import Path

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext

_REPO_ROOT = Path(__file__).resolve().parents[4]
_REVIEW_FIXTURES = _REPO_ROOT / "test_samples" / "fixtures" / "review"


def load_review_fixture(name: str) -> str:
    """Load a unified diff fixture from ``test_samples/fixtures/review``."""
    return (_REVIEW_FIXTURES / name).read_text(encoding="utf-8")


def make_review_context(
    *,
    unified_diff: str,
    changed_files: list[ChangedFile],
    base_ref: str = "base",
    head_ref: str = "head",
    post_image_files: dict[str, str] | None = None,
) -> ReviewContext:
    """Build a review context for chunking tests."""
    return ReviewContext(
        base_ref=base_ref,
        head_ref=head_ref,
        changed_files=changed_files,
        unified_diff=unified_diff,
        pr_metadata=None,
        post_image_files=post_image_files or {},
    )
