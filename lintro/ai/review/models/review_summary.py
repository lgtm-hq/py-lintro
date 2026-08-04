"""Structured PR summary returned by the diff review call."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.summary_bullet import SummaryBullet

__all__ = ["ReviewSummary"]


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    """What the reviewed change does, as a headline plus a walkthrough.

    Attributes:
        headline: One sentence stating what the PR does.
        walkthrough: Three to six bullets walking through the change. May be
            empty when the model returned only a headline.
    """

    headline: str
    walkthrough: tuple[SummaryBullet, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """Return True when neither a headline nor any bullet is present."""
        return not self.headline.strip() and not self.walkthrough
