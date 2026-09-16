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
    """

    id: int
    files: list[str]
    diff: str
    relationship: RelationshipLabel
    metadata_note: str | None = None
    truncated: bool = False
