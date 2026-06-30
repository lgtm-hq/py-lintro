"""AI diff-based code review foundation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.enums import FileDomain
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.group_labels import (
    REL_DIRECTORY_PREFIX,
    REL_SINGLE_FILE,
    REL_SOURCE_TEST,
    REL_WORKFLOW_SCRIPT_TEST,
    RelationshipLabel,
)
from lintro.ai.review.models import (
    ChangedFile,
    ChunkingResult,
    FileClassification,
    PRMetadata,
    ReviewChunk,
    ReviewContext,
)

if TYPE_CHECKING:
    from lintro.ai.review.chunker.grouping import (
        chunk_review_context as chunk_review_context,
    )
    from lintro.ai.review.classifier import (
        classify_changed_files as classify_changed_files,
    )
    from lintro.ai.review.context import (
        collect_review_context as collect_review_context,
    )
    from lintro.ai.review.context import (
        parse_changed_files as parse_changed_files,
    )
    from lintro.ai.review.context import (
        resolve_default_base_branch as resolve_default_base_branch,
    )
    from lintro.ai.review.context import (
        split_unified_diff_by_file as split_unified_diff_by_file,
    )
    from lintro.ai.review.pipeline import prepare_review_chunks as prepare_review_chunks

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "chunk_review_context": (
        "lintro.ai.review.chunker.grouping",
        "chunk_review_context",
    ),
    "classify_changed_files": (
        "lintro.ai.review.classifier",
        "classify_changed_files",
    ),
    "collect_review_context": (
        "lintro.ai.review.context.collection",
        "collect_review_context",
    ),
    "parse_changed_files": (
        "lintro.ai.review.context.diff_parse",
        "parse_changed_files",
    ),
    "prepare_review_chunks": ("lintro.ai.review.pipeline", "prepare_review_chunks"),
    "resolve_default_base_branch": (
        "lintro.ai.review.context.collection",
        "resolve_default_base_branch",
    ),
    "split_unified_diff_by_file": (
        "lintro.ai.review.context.diff_parse",
        "split_unified_diff_by_file",
    ),
}

__all__ = [
    "REL_DIRECTORY_PREFIX",
    "REL_SINGLE_FILE",
    "REL_SOURCE_TEST",
    "REL_WORKFLOW_SCRIPT_TEST",
    "ChangedFile",
    "ChangedFileStatus",
    "ChunkingResult",
    "FileClassification",
    "FileDomain",
    "PRMetadata",
    "RelationshipLabel",
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


def __getattr__(name: str) -> object:
    """Lazily import review submodules to avoid eager cross-layer imports."""
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    return getattr(module, attr_name)
