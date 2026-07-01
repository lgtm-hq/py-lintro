"""Review preparation pipeline chaining context, classification, and chunking."""

from __future__ import annotations

from pathlib import Path

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.context import validate_review_context_diff
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.chunking_result import ChunkingResult
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.prompt_builder import build_review_user_prompt

__all__ = [
    "prepare_review_chunks",
    "prepare_review_user_prompt",
]


def prepare_review_chunks(
    *,
    context: ReviewContext,
    max_tokens: int,
    allow_omitted_files: bool = True,
) -> ChunkingResult:
    """Classify changed files and split review context into semantic chunks.

    Args:
        context: Collected review diff context.
        max_tokens: Maximum estimated tokens per chunk diff.
        allow_omitted_files: When True (default), return omitted repetitive-diff
            files in ``skipped_files`` instead of raising. Pass False for strict
            behavior.

    Returns:
        Chunking result with semantic groups and any truncation metadata.
    """
    validate_review_context_diff(context=context)
    classifications = classify_changed_files(files=context.changed_files)
    return chunk_review_context(
        context=context,
        max_tokens=max_tokens,
        classifications=classifications,
        allow_omitted_files=allow_omitted_files,
    )


def prepare_review_user_prompt(
    *,
    context: ReviewContext,
    checklist_items: list[ChecklistItem],
    diff: str | None = None,
    lint_digest: str | None = None,
    deferred_scope: str | None = None,
    external_flags: list[str] | None = None,
    repo_root: Path | str | None = None,
) -> tuple[str, list[FileClassification], dict[int, int]]:
    """Classify changed files and build the review user prompt.

    Args:
        context: Collected review diff context.
        checklist_items: Selected checklist items for this review.
        diff: Unified diff text to embed. Defaults to ``context.unified_diff``.
        lint_digest: Optional compact lint digest from ``--with-lint``.
        deferred_scope: Optional deferred-scope note from the PR summary.
        external_flags: Optional external review tool flags to verify.
        repo_root: Optional repository root for language tagging.

    Returns:
        Tuple of rendered prompt, file classifications, and prompt-id mapping.
    """
    validate_review_context_diff(context=context)
    classifications = classify_changed_files(files=context.changed_files)
    prompt, prompt_mapping = build_review_user_prompt(
        context=context,
        classifications=classifications,
        checklist_items=checklist_items,
        diff=diff,
        lint_digest=lint_digest,
        deferred_scope=deferred_scope,
        external_flags=external_flags,
        repo_root=repo_root,
    )
    return prompt, classifications, prompt_mapping
