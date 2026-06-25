"""AI diff-based code review foundation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.ai.review.checklist_builtin import BUILTIN_CHECKLIST_ITEMS
from lintro.ai.review.checklist_selector import (
    format_checklist_for_prompt,
    select_checklist_items,
)
from lintro.ai.review.enums import FileDomain, ReviewCategory
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models import (
    ChangedFile,
    ChecklistItem,
    ChunkingResult,
    FileClassification,
    PRMetadata,
    ReviewChunk,
    ReviewContext,
)

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig

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
    "BUILTIN_CHECKLIST_ITEMS",
    "ChangedFile",
    "ChecklistItem",
    "ChunkingResult",
    "FileClassification",
    "FileDomain",
    "PRMetadata",
    "ReviewCategory",
    "ReviewChunk",
    "ReviewContext",
    "ReviewContextError",
    "ReviewContextErrorCode",
    *_LAZY_EXPORTS,
    "format_checklist_for_prompt",
    "get_all_checklist_items",
    "select_checklist_items",
]


def get_all_checklist_items(
    *,
    config: LintroConfig | None = None,
) -> list[ChecklistItem]:
    """Load builtin and custom checklist items.

    Delegates to ``checklist_registry`` to avoid config import cycles during
    package initialization.

    Args:
        config: Loaded Lintro configuration.

    Returns:
        Combined checklist items with custom config entries appended.
    """
    from lintro.ai.review.checklist_registry import (
        get_all_checklist_items as _get_all_checklist_items,
    )

    return _get_all_checklist_items(config=config)


def __getattr__(name: str) -> Any:
    """Lazily import review submodules to avoid eager cross-layer imports."""
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    return getattr(module, attr_name)
