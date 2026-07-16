"""Chunking output with truncation metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.review_chunk import ReviewChunk


@dataclass
class ChunkingResult:
    """Chunking output including truncation warnings.

    Attributes:
        chunks: Ordered review chunks.
        truncated: True when any diff content was trimmed to fit budget.
        warnings: User-facing warnings about trimming or sampling.
        skipped_files: Paths omitted from chunks due to repetitive-diff sampling.
    """

    chunks: list[ReviewChunk]
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
