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
    from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome

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
        sentence = (
            f"{SYNTHESIS_NOTE_LABEL} did not complete; the chunk findings "
            "below are unaffected and cross-chunk duplicate merging was not "
            "applied."
        )
        # A pass that was cut and then failed is both; the coverage banner
        # carries both degradations and the note must not drop one. A failed
        # pass produced nothing the run could use, whether or not the model
        # read the prompt (an unparseable reply and a turn limit both land
        # here), so the note names what it was given, not what it saw.
        if outcome.truncated:
            sentence += (
                f" {_what_it_was_given(outcome=outcome)} (whole-PR token budget)."
            )
        return sentence
    added = outcome.findings_added
    if added == 0:
        body = "found no cross-file inconsistencies"
    elif added == 1:
        body = "added 1 cross-file finding"
    else:
        body = f"added {added} cross-file findings"
    sentence = f"{SYNTHESIS_NOTE_LABEL} {body}."
    if outcome.truncated:
        # Findings coverage is per file and stays complete; what a cut input
        # can miss is a duplicate merge or a cross-file finding across the
        # files it did not see (#2269, #2702).
        sentence += (
            f" {_what_it_saw(outcome=outcome)} (whole-PR token budget); "
            "cross-chunk duplicate merging may be incomplete."
        )
    return sentence


def _what_it_was_given(*, outcome: SynthesisOutcome) -> str:
    """Word how much of the PR a failed pass's prepared input held.

    Args:
        outcome: The pass's outcome, with ``truncated`` and ``failed`` set.

    Returns:
        A clause naming the files whose hunks the input included, or the
        trimmed digest, or, for a record that predates the file counts, that
        the input was cut.
    """
    if outcome.diff_files_included < outcome.diff_files_total:
        return (
            f"Its prepared input held the hunks of {outcome.diff_files_included} "
            f"of {outcome.diff_files_total} changed files"
        )
    if outcome.diff_files_total > 0:
        return "Its prepared input had its finding digest trimmed"
    return "Its prepared input was cut"


def _what_it_saw(*, outcome: SynthesisOutcome) -> str:
    """Word how much of its input a truncated pass was given.

    Args:
        outcome: The pass's outcome, with ``truncated`` set.

    Returns:
        A clause naming the files seen, the trimmed digest, or, for a record
        that predates the file counts, that it saw less than its whole input.
    """
    if outcome.diff_files_included < outcome.diff_files_total:
        return (
            f"It saw {outcome.diff_files_included} of "
            f"{outcome.diff_files_total} changed files, less than its whole "
            "input"
        )
    if outcome.diff_files_total > 0:
        # Every file's diff was kept; what the budget cut was the finding
        # digest in front of it.
        return "Its finding digest was trimmed, so it saw less than its whole input"
    return "It saw less than its whole input"
