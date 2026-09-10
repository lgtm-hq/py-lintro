"""Reasons a finding was tagged as a cross-chunk contradiction (#2265)."""

from __future__ import annotations

from enum import StrEnum, auto


class CrossChunkContradiction(StrEnum):
    """Why a finding contradicts the diff the review actually ran on.

    A chunked review shows each chunk the other files at the base commit, so a
    finding written from one chunk can assert that a file changed elsewhere in
    the same pull request was never touched. The assertion is checkable
    without asking the model anything: the changed-file set is known, so a
    claim that contradicts it marks the finding as chunk-local rather than
    wrong-in-general.

    Attributes:
        UNCHANGED_FILE_CLAIM_DOWNGRADED: The finding's own text claims a file
            that is in the diff was never touched, and the finding was moved
            one severity band down (P1 to P2, P2 to P3).
        UNCHANGED_FILE_CLAIM_TAGGED: The same claim on a P3 finding, which
            has no lower band; it is tagged and counted but its severity is
            unchanged.
    """

    UNCHANGED_FILE_CLAIM_DOWNGRADED = auto()
    UNCHANGED_FILE_CLAIM_TAGGED = auto()
