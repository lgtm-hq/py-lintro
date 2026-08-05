"""Reasons a committable suggestion was not offered on an inline comment."""

from __future__ import annotations

from enum import StrEnum, auto


class SuggestionRejection(StrEnum):
    """Why mode A was rejected in favour of mode B (#1911).

    GitHub silently drops an invalid ``suggestion`` block — or rejects the
    whole review batch — so every rejection is named rather than folded into a
    single boolean, and the reason is logged when a fix that looked mechanical
    could not be offered as one click.

    Attributes:
        NO_SUGGESTED_CHANGE: The finding carries no replacement text at all.
        EMPTY_REPLACEMENT: The replacement is blank, which would delete the
            anchored lines rather than fix them.
        INVALID_RANGE: The line range is not a positive, ordered span.
        SPAN_TOO_LARGE: The range names more lines than one suggestion may
            plausibly replace; expanding it would be the model's decision, not
            the renderer's.
        REPLACEMENT_TOO_LARGE: The replacement is big enough to threaten
            GitHub's comment size limit, which would cost the reader the
            reasoning as well as the suggestion.
        ANCHOR_OUTSIDE_RANGE: The finding's own line falls outside the range,
            so the comment could not be anchored to exactly those lines.
        CARRIED_OVER: The finding was already reported in an earlier round; its
            thread is not anchored to this round's posted diff.
        NO_ROUND_DIFF: This round's diff could not be determined, so no line
            can be shown to be committable.
        LINES_NOT_IN_ROUND_DIFF: At least one replaced line was not changed by
            this round's posted diff.
    """

    NO_SUGGESTED_CHANGE = auto()
    EMPTY_REPLACEMENT = auto()
    INVALID_RANGE = auto()
    SPAN_TOO_LARGE = auto()
    REPLACEMENT_TOO_LARGE = auto()
    ANCHOR_OUTSIDE_RANGE = auto()
    CARRIED_OVER = auto()
    NO_ROUND_DIFF = auto()
    LINES_NOT_IN_ROUND_DIFF = auto()
