"""AI diff-based code review foundation."""

from __future__ import annotations

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.context import (
    collect_review_context,
    parse_changed_files,
    resolve_default_base_branch,
    split_unified_diff_by_file,
)
from lintro.ai.review.enums import FileDomain
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models import (
    ChangedFile,
    ChunkingResult,
    FileClassification,
    PRMetadata,
    ReviewChunk,
    ReviewContext,
)
from lintro.ai.review.pipeline import prepare_review_chunks

__all__ = [
    "ChangedFile",
    "ChunkingResult",
    "FileClassification",
    "FileDomain",
    "PRMetadata",
    "ReviewChunk",
    "ReviewContext",
    "ReviewContextError",
    "ReviewContextErrorCode",
    "chunk_review_context",
    "classify_changed_files",
    "collect_review_context",
    "parse_changed_files",
    "prepare_review_chunks",
    "resolve_default_base_branch",
    "split_unified_diff_by_file",
]
