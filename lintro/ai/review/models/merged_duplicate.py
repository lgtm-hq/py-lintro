"""One finding a duplicate merge folded into another (lintro-ops #37)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["MergedDuplicate"]


@dataclass(frozen=True, slots=True)
class MergedDuplicate:
    """Identity of a finding the synthesis pass merged into a survivor.

    A duplicate merge re-attributes a defect to its root cause; it does not
    repair one. The losing side leaves the round's finding list, so without a
    record of what it was the matcher would find no current sighting of the
    finding's prior record and stamp a live defect resolved. Only identity is
    carried — the prose stays on the survivor — because identity is all
    :func:`~lintro.ai.review.finding_matcher.fingerprint_for` needs to pair
    the merged-away finding with the record it opened.

    Attributes:
        file: Repository-relative path the merged finding was reported at.
        category: Its category label.
        title: Its title.
        line: The line it was reported at, used to break ties when several
            prior records share a fingerprint.
    """

    file: str
    category: str
    title: str
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the merged duplicate for JSON and MCP payloads.

        Returns:
            JSON-serializable mapping naming the merged-away finding.
        """
        return {
            "file": self.file,
            "category": self.category,
            "title": self.title,
            "line": self.line,
        }
