"""Compact per-chunk digest fed to the cross-chunk synthesis pass (#2269)."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = ["ChunkSummary"]


@dataclass(frozen=True, slots=True)
class ChunkSummary:
    """What one review chunk covered and what it already reported.

    Deliberately narrow: the synthesis pass needs to know which files were
    reviewed together and which problems are already on the record, not the
    chunk's prose. Keeping the digest small is what leaves room in the pass's
    token budget for the diff it actually has to reason over.

    Attributes:
        chunk_id: One-based chunk identifier, matching ``ReviewChunk.id``.
        files: Repository-relative paths the chunk reviewed, in chunk order.
        findings: Findings the chunk reported, in reported order.
    """

    chunk_id: int
    files: tuple[str, ...] = field(default_factory=tuple)
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
