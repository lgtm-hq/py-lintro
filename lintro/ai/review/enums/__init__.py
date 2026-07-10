"""Enumerations for AI diff review."""

from __future__ import annotations

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode

__all__ = [
    "ChangedFileStatus",
    "FileDomain",
    "ReviewCategory",
    "ReviewContextErrorCode",
]
