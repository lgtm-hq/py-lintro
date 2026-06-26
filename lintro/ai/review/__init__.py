"""AI diff-based code review foundation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from lintro.ai.review.chunker import chunk_review_context
    from lintro.ai.review.classifier import classify_changed_files
    from lintro.ai.review.context import (
        collect_review_context,
        parse_changed_files,
        resolve_default_base_branch,
        split_unified_diff_by_file,
    )
    from lintro.ai.review.pipeline import prepare_review_chunks

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "chunk_review_context": ("lintro.ai.review.chunker", "chunk_review_context"),
    "classify_changed_files": (
        "lintro.ai.review.classifier",
        "classify_changed_files",
    ),
    "collect_review_context": ("lintro.ai.review.context", "collect_review_context"),
    "parse_changed_files": ("lintro.ai.review.context", "parse_changed_files"),
    "prepare_review_chunks": ("lintro.ai.review.pipeline", "prepare_review_chunks"),
    "resolve_default_base_branch": (
        "lintro.ai.review.context",
        "resolve_default_base_branch",
    ),
    "split_unified_diff_by_file": (
        "lintro.ai.review.context",
        "split_unified_diff_by_file",
    ),
}

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
    *_LAZY_EXPORTS,
    "chunk_review_context",
    "classify_changed_files",
    "collect_review_context",
    "parse_changed_files",
    "resolve_default_base_branch",
    "split_unified_diff_by_file",
    "prepare_review_chunks",
]


def __getattr__(name: str) -> Any:
    """Lazily import review submodules to avoid eager cross-layer imports."""
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    return getattr(module, attr_name)
