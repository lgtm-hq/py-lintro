"""Semantic chunking for AI diff review."""

from __future__ import annotations

from lintro.ai.review.chunker.grouping import (
    _hunk_signature,
    _prune_semantic_groups,
    chunk_review_context,
)

__all__ = [
    "_hunk_signature",
    "_prune_semantic_groups",
    "chunk_review_context",
]
