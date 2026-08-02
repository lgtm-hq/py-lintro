"""Single walkthrough bullet of a structured review summary."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SummaryBullet"]


@dataclass(frozen=True, slots=True)
class SummaryBullet:
    """One walkthrough bullet describing part of the reviewed change.

    Attributes:
        text: The bullet text — one sentence about a coherent part of the PR.
        finding_ref: Optional reference to the finding this bullet is about,
            in ``file:line`` form. Renderers resolve it against the run's
            findings so bullets that correspond to open P1/P2 findings can be
            severity-marked instead of reading as neutral prose. Empty when the
            bullet does not correspond to a finding.
    """

    text: str
    finding_ref: str = ""
