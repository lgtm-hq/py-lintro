"""Enumerations for AI diff review."""

from __future__ import annotations

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.enums.review_verdict import ReviewVerdict

__all__ = [
    "ChangedFileStatus",
    "FileDomain",
    "FindingMatchOutcome",
    "FindingStatus",
    "ReviewCategory",
    "ReviewContextErrorCode",
    "ReviewVerdict",
]
