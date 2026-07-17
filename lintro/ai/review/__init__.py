"""AI diff-based code review foundation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.ai.review.checklist import (
        BUILTIN_CHECKLIST_ITEMS as BUILTIN_CHECKLIST_ITEMS,
    )
    from lintro.ai.review.checklist_registry import (
        get_all_checklist_items as get_all_checklist_items,
    )
    from lintro.ai.review.checklist_selector import (
        format_checklist_for_prompt as format_checklist_for_prompt,
    )
    from lintro.ai.review.checklist_selector import (
        select_checklist_items as select_checklist_items,
    )
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
    from lintro.ai.review.enums import FileDomain as FileDomain
    from lintro.ai.review.enums import ReviewCategory as ReviewCategory
    from lintro.ai.review.enums.changed_file_status import (
        ChangedFileStatus as ChangedFileStatus,
    )
    from lintro.ai.review.enums.review_context_error_code import (
        ReviewContextErrorCode as ReviewContextErrorCode,
    )
    from lintro.ai.review.exceptions import ReviewContextError as ReviewContextError
    from lintro.ai.review.group_labels import (
        REL_DIRECTORY_PREFIX as REL_DIRECTORY_PREFIX,
    )
    from lintro.ai.review.group_labels import REL_SINGLE_FILE as REL_SINGLE_FILE
    from lintro.ai.review.group_labels import REL_SOURCE_TEST as REL_SOURCE_TEST
    from lintro.ai.review.group_labels import (
        REL_WORKFLOW_SCRIPT_TEST as REL_WORKFLOW_SCRIPT_TEST,
    )
    from lintro.ai.review.group_labels import RelationshipLabel as RelationshipLabel
    from lintro.ai.review.models import ChangedFile as ChangedFile
    from lintro.ai.review.models import ChecklistItem as ChecklistItem
    from lintro.ai.review.models import ChunkingResult as ChunkingResult
    from lintro.ai.review.models import FileClassification as FileClassification
    from lintro.ai.review.models import PRMetadata as PRMetadata
    from lintro.ai.review.models import ReviewChunk as ReviewChunk
    from lintro.ai.review.models import ReviewContext as ReviewContext
    from lintro.ai.review.pipeline import (
        prepare_review_chunks as prepare_review_chunks,
    )
    from lintro.ai.review.pipeline import (
        prepare_review_user_prompt as prepare_review_user_prompt,
    )

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BUILTIN_CHECKLIST_ITEMS": (
        "lintro.ai.review.checklist",
        "BUILTIN_CHECKLIST_ITEMS",
    ),
    "ChangedFile": ("lintro.ai.review.models", "ChangedFile"),
    "ChangedFileStatus": (
        "lintro.ai.review.enums.changed_file_status",
        "ChangedFileStatus",
    ),
    "ChecklistItem": ("lintro.ai.review.models", "ChecklistItem"),
    "ChunkingResult": ("lintro.ai.review.models", "ChunkingResult"),
    "FileClassification": ("lintro.ai.review.models", "FileClassification"),
    "FileDomain": ("lintro.ai.review.enums", "FileDomain"),
    "PRMetadata": ("lintro.ai.review.models", "PRMetadata"),
    "REL_DIRECTORY_PREFIX": ("lintro.ai.review.group_labels", "REL_DIRECTORY_PREFIX"),
    "REL_SINGLE_FILE": ("lintro.ai.review.group_labels", "REL_SINGLE_FILE"),
    "REL_SOURCE_TEST": ("lintro.ai.review.group_labels", "REL_SOURCE_TEST"),
    "REL_WORKFLOW_SCRIPT_TEST": (
        "lintro.ai.review.group_labels",
        "REL_WORKFLOW_SCRIPT_TEST",
    ),
    "RelationshipLabel": ("lintro.ai.review.group_labels", "RelationshipLabel"),
    "ReviewCategory": ("lintro.ai.review.enums", "ReviewCategory"),
    "ReviewChunk": ("lintro.ai.review.models", "ReviewChunk"),
    "ReviewContext": ("lintro.ai.review.models", "ReviewContext"),
    "ReviewContextError": ("lintro.ai.review.exceptions", "ReviewContextError"),
    "ReviewContextErrorCode": (
        "lintro.ai.review.enums.review_context_error_code",
        "ReviewContextErrorCode",
    ),
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
    "format_checklist_for_prompt": (
        "lintro.ai.review.checklist_selector",
        "format_checklist_for_prompt",
    ),
    "get_all_checklist_items": (
        "lintro.ai.review.checklist_registry",
        "get_all_checklist_items",
    ),
    "parse_changed_files": (
        "lintro.ai.review.context.diff_parse",
        "parse_changed_files",
    ),
    "prepare_review_chunks": ("lintro.ai.review.pipeline", "prepare_review_chunks"),
    "prepare_review_user_prompt": (
        "lintro.ai.review.pipeline",
        "prepare_review_user_prompt",
    ),
    "resolve_default_base_branch": (
        "lintro.ai.review.context.collection",
        "resolve_default_base_branch",
    ),
    "select_checklist_items": (
        "lintro.ai.review.checklist_selector",
        "select_checklist_items",
    ),
    "split_unified_diff_by_file": (
        "lintro.ai.review.context.diff_parse",
        "split_unified_diff_by_file",
    ),
}

__all__ = [
    "BUILTIN_CHECKLIST_ITEMS",
    "REL_DIRECTORY_PREFIX",
    "REL_SINGLE_FILE",
    "REL_SOURCE_TEST",
    "REL_WORKFLOW_SCRIPT_TEST",
    "ChangedFile",
    "ChangedFileStatus",
    "ChecklistItem",
    "ChunkingResult",
    "FileClassification",
    "FileDomain",
    "PRMetadata",
    "RelationshipLabel",
    "ReviewCategory",
    "ReviewChunk",
    "ReviewContext",
    "ReviewContextError",
    "ReviewContextErrorCode",
    "chunk_review_context",
    "classify_changed_files",
    "collect_review_context",
    "format_checklist_for_prompt",
    "get_all_checklist_items",
    "parse_changed_files",
    "prepare_review_chunks",
    "prepare_review_user_prompt",
    "resolve_default_base_branch",
    "select_checklist_items",
    "split_unified_diff_by_file",
]


def __getattr__(name: str) -> Any:
    """Lazily import review submodules to avoid eager cross-layer imports."""
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
