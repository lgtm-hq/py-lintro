"""Shared wording for the cross-chunk synthesis pass note (#2269).

Every surface (terminal, GitHub review body, sticky comment) describes the
extra pass with the same sentence built here, so a round that gained a
cross-file finding can never read as an ordinary chunk-only round on one
surface and as something else on another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.review.models.review_metadata import ReviewMetadata

__all__ = ["SYNTHESIS_NOTE_LABEL", "format_synthesis_note"]

#: Short lead-in reused wherever the note is rendered with a label.
SYNTHESIS_NOTE_LABEL = "Cross-chunk synthesis"


def format_synthesis_note(*, metadata: ReviewMetadata) -> str:
    """Describe what the cross-chunk synthesis pass did on this run.

    Args:
        metadata: Review run metadata carrying ``synthesis``.

    Returns:
        A plain-text sentence, or an empty string when the pass did not run —
        which is the default and every run before the pass existed. The text
        carries no markup so the terminal and the GitHub surfaces can share it
        verbatim.
    """
    outcome = metadata.synthesis
    if outcome is None:
        return ""
    if outcome.failed:
        return (
            f"{SYNTHESIS_NOTE_LABEL} did not complete; the chunk findings "
            "below are unaffected."
        )
    added = outcome.findings_added
    if added == 0:
        body = "found no cross-file inconsistencies"
    elif added == 1:
        body = "added 1 cross-file finding"
    else:
        body = f"added {added} cross-file findings"
    sentence = f"{SYNTHESIS_NOTE_LABEL} {body}."
    if outcome.truncated:
        sentence += (
            " Its input was truncated to the whole-PR token budget, so it saw "
            "only part of the diff."
        )
    return sentence
