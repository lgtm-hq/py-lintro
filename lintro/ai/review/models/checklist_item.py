"""Checklist item model for AI diff review."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.review_category import ReviewCategory


@dataclass(frozen=True)
class ChecklistItem:
    """A single review checklist question.

    Attributes:
        id: Stable checklist identifier. Tier 1 uses 1-15, Tier 2 uses 100+,
            and custom config items use 10000+.
        question: Yes/no question posed to the review model.
        triggers: File globs that activate Tier 2 and custom items. Empty means
            Tier 1 always-on items.
        category: Finding category when the answer is yes.
        tier: Checklist tier (1 for always-on, 2 for trigger-selected).
    """

    id: int
    question: str
    triggers: list[str]
    category: ReviewCategory
    tier: int
