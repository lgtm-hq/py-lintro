"""Checklist item model for AI diff review."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory


@dataclass(frozen=True)
class ChecklistItem:
    """A single review checklist question.

    Items activate on two axes derived from the changed files: ``domains`` are
    role labels owned by the file classifier (CI, TEST, DOCS, ...), and
    ``languages`` are ``identify`` tags (``rust``, ``ts``, ``python``, ...). A
    Tier 2 item is selected when its domains intersect the diff's domains or its
    languages intersect the diff's languages. A Tier 2 item with both axes empty
    is universal and applies to any non-empty diff.

    Attributes:
        id: Stable checklist identifier. Tier 1 uses 1-15, Tier 2 uses 100+,
            and custom config items use 10000+.
        question: Yes/no question posed to the review model.
        domains: Role domains that activate the item. Empty for Tier 1.
        languages: ``identify`` language tags that activate the item. Empty for
            Tier 1.
        category: Finding category when the answer is yes.
        tier: Checklist tier (1 for always-on, 2 for selection-triggered).
    """

    id: int
    question: str
    domains: tuple[FileDomain, ...]
    languages: tuple[str, ...]
    category: ReviewCategory
    tier: int
