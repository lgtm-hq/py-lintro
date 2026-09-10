"""Everything one batch of inline finding comments is posted with (#2305)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = ["InlinePostRequest"]


@dataclass(frozen=True, kw_only=True, slots=True)
class InlinePostRequest:
    """Inputs for the single review submission a round makes.

    Attributes:
        findings: Diff-mappable findings to anchor as inline comments.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        review_body: Markdown body posted with the review. Falls back to a
            plain label when empty.
        round_diff_lines: Lines changed by this round's posted diff. ``None``
            disables committable suggestions entirely.
        carried_fingerprints: Fingerprints of findings already reported in an
            earlier round; they fall back to a described fix.
        finding_keys: Identity key per finding, in the same order, embedded as
            a hidden marker so the posted comment can be recognized later.
        provenance: Finding key to a provenance note prepended to its comment.
            A regression is re-raised on a fresh thread, and the note is what
            keeps it from reading as a brand-new finding.
    """

    findings: Sequence[ReviewFinding] = ()
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF
    question_map: Mapping[int, str] = field(default_factory=dict)
    review_body: str = ""
    round_diff_lines: dict[str, set[int]] | None = None
    carried_fingerprints: frozenset[str] = frozenset()
    finding_keys: Sequence[str] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
