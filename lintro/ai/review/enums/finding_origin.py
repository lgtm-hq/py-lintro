"""Provenance of a review finding beyond the ordinary chunk pass (#2269)."""

from __future__ import annotations

from enum import StrEnum, auto


class FindingOrigin(StrEnum):
    """Which review pass produced a finding.

    Ordinary chunk findings carry no origin at all: the attribute is ``None``
    for them, so a run without the extra pass serializes byte-identically to
    one from before this enum existed. Only a pass that a reader would
    otherwise be unable to attribute gets a member here.

    Attributes:
        SYNTHESIS: The final cross-chunk synthesis pass, which sees the merged
            chunk findings and the whole-PR file list and reports only
            inconsistencies *between* files reviewed in different chunks.
    """

    SYNTHESIS = auto()
