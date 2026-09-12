"""Confidence gate on inline posting (#2572).

Findings carry a model-reported ``confidence`` (high, medium, low) and a
``kind`` (finding or question). Before this gate both were display-only: every
finding opened an inline review thread the author had to resolve, questions and
low-confidence guesses included. Under a zero-unresolved-threads merge rule that
turns "I am not sure about this" into a blocker.

The policy is mechanical, like the severity gates in
:mod:`lintro.ai.review.severity_gate`: a finding is posted inline when its
confidence meets the configured floor and, unless questions are opted in, it is
a defect claim rather than a question. Everything else becomes a *note*. Nothing
is dropped — a note keeps its prose and its place in the JSON and MCP payloads —
it is only routed to the sticky comment's collapsed "Notes and questions" block,
where it never feeds the derived verdict or the header counts.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.enums.confidence_level import ConfidenceLevel

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = [
    "PostingPolicy",
    "apply_posting_policy",
    "coerce_confidence",
    "describe_notes",
    "inline_findings",
    "note_findings",
    "select_inline",
]


def coerce_confidence(*, raw: object) -> ConfidenceLevel:
    """Normalize a model-reported confidence value to a level.

    Args:
        raw: Raw ``confidence`` value as parsed from the model response.

    Returns:
        The matching level. An unrecognized value reads as ``MEDIUM``, the
        same fallback :mod:`lintro.ai.review.finding_parser` applies when the
        model omits the field, so a misspelled confidence neither hides a
        finding nor promotes it.
    """
    if isinstance(raw, ConfidenceLevel):
        return raw
    if isinstance(raw, str):
        try:
            return ConfidenceLevel(raw.strip().lower())
        except ValueError:
            return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.MEDIUM


@dataclass(frozen=True, slots=True)
class PostingPolicy:
    """What a finding must satisfy to be posted as an inline thread.

    Attributes:
        inline_min_confidence: Lowest confidence still posted inline. A
            finding below it becomes a note. Defaults to ``MEDIUM``, so only
            ``low`` is routed away out of the box.
        post_questions_inline: Whether question-kind entries open inline
            threads. Off by default: a question is suspicion without proof
            and belongs in the notes block, not in a thread that blocks the
            merge until someone resolves it.
    """

    inline_min_confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    post_questions_inline: bool = False

    @classmethod
    def from_ai_config(cls, ai_config: AIConfig) -> PostingPolicy:
        """Build the policy from the resolved AI configuration.

        Args:
            ai_config: Resolved AI configuration for this run.

        Returns:
            The policy ``ai.review_inline_min_confidence`` and
            ``ai.review_post_questions_inline`` describe.
        """
        return cls(
            inline_min_confidence=ai_config.review_inline_min_confidence,
            post_questions_inline=ai_config.review_post_questions_inline,
        )

    def posts_inline(self, *, finding: ReviewFinding) -> bool:
        """Return True when the policy posts this finding inline.

        Args:
            finding: Finding to test.

        Returns:
            True when the finding's confidence meets the floor and it is
            either a defect claim or a question the policy lets through.
        """
        if finding.is_question and not self.post_questions_inline:
            return False
        confidence = coerce_confidence(raw=finding.confidence)
        return confidence.numeric_order >= self.inline_min_confidence.numeric_order


def select_inline(
    findings: Sequence[ReviewFinding],
    policy: PostingPolicy,
) -> tuple[tuple[ReviewFinding, ...], tuple[ReviewFinding, ...]]:
    """Split findings into the inline set and the notes set.

    Args:
        findings: Findings as reported by the model, in payload order.
        policy: Posting policy to apply.

    Returns:
        ``(inline, notes)``. Each preserves the input order, and every
        finding lands in exactly one of the two.
    """
    inline: list[ReviewFinding] = []
    notes: list[ReviewFinding] = []
    for finding in findings:
        if policy.posts_inline(finding=finding):
            inline.append(finding)
        else:
            notes.append(finding)
    return tuple(inline), tuple(notes)


def apply_posting_policy(
    *,
    findings: Sequence[ReviewFinding],
    policy: PostingPolicy,
) -> tuple[ReviewFinding, ...]:
    """Mark each finding with the policy's routing decision.

    The decision is carried on the finding itself (``posted_inline``) rather
    than as a separate list so every surface — inline posting, the sticky, the
    review body, JSON, MCP — reads one answer instead of recomputing it.

    Args:
        findings: Findings as reported by the model, in payload order.
        policy: Posting policy to apply.

    Returns:
        The same findings in the same order, with ``posted_inline`` set from
        the policy on each.
    """
    return tuple(
        replace(finding, posted_inline=policy.posts_inline(finding=finding))
        for finding in findings
    )


def inline_findings(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings routed to inline threads.

    Args:
        findings: Findings to filter.

    Returns:
        The findings with ``posted_inline`` set, in the order given.
    """
    return tuple(finding for finding in findings if finding.posted_inline)


def note_findings(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings routed to the sticky notes block.

    Args:
        findings: Findings to filter.

    Returns:
        The findings with ``posted_inline`` cleared, in the order given.
    """
    return tuple(finding for finding in findings if not finding.posted_inline)


def describe_notes(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the notes block's summary label.

    Args:
        findings: Findings to count notes over.

    Returns:
        ``"Notes and questions (N)"``, or an empty string when nothing was
        routed to the notes block.
    """
    count = len(note_findings(findings=findings))
    if not count:
        return ""
    return f"Notes and questions ({count})"
