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
    """

    id: int
    files: list[str]
    diff: str
    relationship: RelationshipLabel
    metadata_note: str | None = None
