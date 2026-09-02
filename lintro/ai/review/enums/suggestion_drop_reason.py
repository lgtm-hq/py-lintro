"""Reasons a suggested patch was dropped before posting (#2101)."""

from __future__ import annotations

from enum import StrEnum, auto


class SuggestionDropReason(StrEnum):
    """Why a finding's suggested patch failed validation against head.

    A committable ``suggestion`` block that does not match the file it is
    anchored to is worse than no suggestion at all: applied, it corrupts the
    file. Validation therefore strips the block and records *why*, so the drop
    is visible on every surface instead of the suggestion quietly vanishing.

    Attributes:
        FILE_MISSING: The finding's file could not be read at the head
            revision, so nothing can confirm the patch still applies.
        STALE_ANCHOR: The named line range does not exist at head, or the
            change's ``before`` block was not found anywhere in the file.
        AMBIGUOUS_ANCHOR: The ``before`` block occurs more than once at head,
            so re-anchoring would be a guess between equally good matches.
    """

    FILE_MISSING = auto()
    STALE_ANCHOR = auto()
    AMBIGUOUS_ANCHOR = auto()
