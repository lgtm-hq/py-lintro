"""Parsers for the AI-powered ``idiom-review`` tool.

Exposes the issue dataclass and the response parser used to turn raw AI
provider responses into structured :class:`IdiomReviewIssue` objects.
"""

from __future__ import annotations

from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue
from lintro.parsers.idiom_review.idiom_review_parser import IdiomReviewParser

__all__ = ["IdiomReviewIssue", "IdiomReviewParser"]
