"""Chunking output with truncation metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.skipped_file import SkippedFile


@dataclass
class ChunkingResult:
    """Chunking output including truncation warnings.

    Attributes:
        chunks: Ordered review chunks.
        truncated: True when any diff content was trimmed to fit budget.
        warnings: User-facing warnings about trimming or sampling.
        skipped: Files omitted from chunks, each carrying why it was omitted.
    """

    chunks: list[ReviewChunk]
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    @property
    def skipped_files(self) -> list[str]:
        """Return the paths omitted from chunks, without their reasons."""
        return [entry.path for entry in self.skipped]
