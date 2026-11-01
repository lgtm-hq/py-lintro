"""Fine-print notes for the optional review passes, shared by two surfaces.

The review body and the sticky's run-stats block both show the synthesis
(#2269), verification (#2728) and narrative degradation (#2803) notes in this
order; one helper keeps the two surfaces identical. Split from
:mod:`lintro.ai.review.github_notes` so that module stays under the review
package's size ratchet.
"""

from __future__ import annotations

from lintro.ai.review.coverage_degradation import format_narrative_note
from lintro.ai.review.github_notes import (
    format_synthesis_note_line,
    format_verification_note_line,
)
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = ["format_narrative_note_line", "format_pass_note_lines"]


def format_pass_note_lines(*, metadata: ReviewMetadata) -> list[str]:
    """Return the optional-pass fine-print notes, each after a blank line.

    The review body and the sticky's run-stats block both show the synthesis
    (#2269), verification (#2728) and narrative degradation (#2803) notes in
    this order; one helper keeps the two surfaces identical.

    Args:
        metadata: Review run metadata.

    Returns:
        ``["", note, ...]`` for each note that applies; empty when none do,
        so a default round's block is byte-identical to before.
    """
    notes = (
        format_synthesis_note_line(metadata=metadata),
        format_verification_note_line(metadata=metadata),
        format_narrative_note_line(metadata=metadata),
    )
    # Each helper renders at most one line; the pair is a blank line and it.
    return [part for note in notes if note for part in ("", note)]


def format_narrative_note_line(*, metadata: ReviewMetadata) -> str:
    """Render the narrative degradation note as a fine-print line (#2803).

    Args:
        metadata: Review run metadata carrying ``coverage_degradations``.

    Returns:
        The note wrapped in ``<sub>``, or an empty string when there is no
        narrative degradation to report.
    """
    note = format_narrative_note(metadata=metadata)
    if not note:
        return ""
    return f"<sub>{sanitize_comment_text(note, limit=600)}</sub>"
