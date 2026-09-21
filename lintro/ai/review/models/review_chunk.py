"""Semantic review chunk container."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.group_labels import RelationshipLabel


@dataclass
class ReviewChunk:
    """A semantically grouped diff chunk for model review.

    Attributes:
        id: One-based chunk identifier.
        files: Repository-relative paths included in the chunk.
        diff: Unified diff text for the chunk.
        relationship: Valid semantic grouping label.
        metadata_note: Optional note for sampled or truncated content.
        truncated: True when the chunk's diff was cut to fit the hard token
            ceiling, so the model saw only a prefix of the file's change.
        read_diff: On a delta round (#2627), the text the prompt embeds —
            each queued file's change since the prior round's head — where
            ``diff`` stays the whole-PR hunk the diff gate, the cross-chunk
            guard and the budgets see. ``None`` on a full round.
    """

    id: int
    files: list[str]
    diff: str
    relationship: RelationshipLabel
    metadata_note: str | None = None
    truncated: bool = False
    read_diff: str | None = None
