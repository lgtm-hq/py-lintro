"""Shared wording for the verification pass note (#2728).

Like the synthesis note, one sentence built here and rendered verbatim on
every surface, so a round the verifier changed reads the same on the
terminal, in the review body and in the sticky comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.agent_prompt_text import plural

if TYPE_CHECKING:
    from lintro.ai.review.models.review_metadata import ReviewMetadata

__all__ = ["VERIFICATION_NOTE_LABEL", "format_verification_note"]

#: Short lead-in reused wherever the note is rendered with a label.
VERIFICATION_NOTE_LABEL = "Verification"


def format_verification_note(*, metadata: ReviewMetadata) -> str:
    """Describe what the verification pass did on this run.

    Args:
        metadata: Review run metadata carrying ``verification``.

    Returns:
        A plain-text sentence, or an empty string when the pass did not run
        or had nothing to check, so a round with no P1 and no low-confidence
        finding renders exactly as it did before the pass existed.
    """
    summary = metadata.verification
    if summary is None or not summary.enabled or summary.selected == 0:
        return ""
    checked = plural(count=summary.selected, noun="finding")
    if summary.failed:
        return (
            f"{VERIFICATION_NOTE_LABEL} did not complete; {checked} kept " "unverified."
        )
    parts: list[str] = []
    if summary.confirmed:
        parts.append(f"{summary.confirmed} confirmed")
    if summary.refuted:
        parts.append(f"{summary.refuted} refuted and dropped")
    if summary.downgraded:
        parts.append(f"{summary.downgraded} moved to P2")
    if not parts:
        # Every selected finding came back without a verdict.
        return f"{VERIFICATION_NOTE_LABEL} re-checked {checked}: none answered."
    return f"{VERIFICATION_NOTE_LABEL} re-checked {checked}: {', '.join(parts)}."
