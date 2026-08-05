"""A changed file the review deliberately did not look at."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.file_skip_reason import (
    FileSkipReason,
    describe_skip_reason,
)

__all__ = ["SkippedFile"]


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """One changed file excluded from the review, with why it was excluded.

    Attributes:
        path: Repository-relative path of the excluded file.
        reason: Which selection rule dropped it.
        detail: Optional extra context (for example the file whose diff it
            duplicated). Empty when the reason alone is the whole story.
    """

    path: str
    reason: FileSkipReason
    detail: str = ""

    @property
    def label(self) -> str:
        """Return the reason phrase, including ``detail`` when present."""
        base = describe_skip_reason(reason=self.reason)
        return f"{base} — {self.detail}" if self.detail else base
