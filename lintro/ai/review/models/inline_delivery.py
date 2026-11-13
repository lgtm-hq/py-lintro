"""What a round's GitHub post actually delivered inline (#3426)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult

__all__ = ["InlineDelivery"]


@dataclass(slots=True)
class InlineDelivery:
    """Record of the inline comments a post put on the pull request.

    ``posted_inline`` on a finding says the posting policy selected it for a
    thread. It is set before anything is posted, so it cannot say whether a
    thread exists. The poster fills this record as it goes, and
    :meth:`stamp` copies the outcome onto each finding's ``inline_delivered``
    so the JSON surface can report delivery separately from eligibility.

    The record is mutable on purpose: like ``captured_comment_ids`` it is a
    sink the caller hands to the poster and reads back afterwards.

    Attributes:
        attempted: True once the poster reached GitHub and began posting.
            Stays False for a run that never posted (no ``--post``, no
            GitHub context, or a review the runner's SIGTERM stopped before
            posting began), so every finding reports
            ``inline_delivered: null``. A SIGTERM that lands *during*
            posting comes after this is set, so findings report ``false``
            unless their inline review was already confirmed.
        delivered: Findings whose inline comment GitHub accepted. Matched by
            identity, so two findings with equal fields are never confused.
    """

    attempted: bool = False
    delivered: list[ReviewFinding] = field(default_factory=list)

    def stamp(self, *, result: ReviewResult) -> ReviewResult:
        """Return *result* with each finding's ``inline_delivered`` set.

        Args:
            result: The result whose findings were handed to the poster.

        Returns:
            The same result when posting was not attempted (every finding
            keeps ``None``). Otherwise a copy whose findings carry ``True``
            when their inline comment was accepted and ``False`` otherwise.
        """
        if not self.attempted:
            return result
        accepted = {id(finding) for finding in self.delivered}
        return replace(
            result,
            findings=tuple(
                replace(finding, inline_delivered=id(finding) in accepted)
                for finding in result.findings
            ),
        )
