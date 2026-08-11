"""Review user prompt construction for AI diff review."""

from __future__ import annotations

from pathlib import Path

from lintro.ai.prompts.review import (
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_deferred_scope_section,
    format_external_review_section,
    format_lint_results_section,
    format_output_rules,
)
from lintro.ai.review.checklist_selector import format_checklist_for_prompt
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.paths_registry import generate_interaction_paths
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.sanitize import make_boundary_marker

__all__ = ["build_review_user_prompt"]


def build_review_user_prompt(
    *,
    context: ReviewContext,
    classifications: list[FileClassification],
    checklist_items: list[ChecklistItem],
    diff: str | None = None,
    lint_digest: str | None = None,
    deferred_scope: str | None = None,
    external_flags: list[str] | None = None,
    repo_root: Path | str | None = None,
) -> tuple[str, dict[int, int]]:
    """Build the review user prompt with interaction paths and checklist.

    Args:
        context: Collected review diff context.
        classifications: Per-file domain classifications for changed files.
        checklist_items: Selected checklist items for this review.
        diff: Unified diff text to embed. Defaults to ``context.unified_diff``.
        lint_digest: Optional compact lint digest from ``--with-lint``.
        deferred_scope: Optional deferred-scope note from the PR summary.
        external_flags: Optional external review tool flags to verify.
        repo_root: Optional repository root for language tagging.

    Returns:
        Tuple of rendered user prompt and prompt-id to checklist-id mapping.
    """
    changed_paths = [file.path for file in context.changed_files]
    interaction_paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=changed_paths,
        repo_root=repo_root,
    )
    checklist_text, prompt_mapping = format_checklist_for_prompt(
        items=checklist_items,
    )
    pr_title = (
        context.pr_metadata.title
        if context.pr_metadata is not None
        else f"{context.base_ref}...{context.head_ref}"
    )
    pr_summary = context.pr_metadata.body if context.pr_metadata is not None else ""
    raw_diff = diff if diff is not None else context.unified_diff
    boundary = make_boundary_marker()
    deferred_section = format_deferred_scope_section(text=deferred_scope)
    if deferred_section:
        # Deferred scope derives from the PR summary — untrusted; fence it
        # like the other PR-controlled fields (#1884).
        deferred_section = (
            f"<{boundary}>\n"
            + redact_prompt_text(text=deferred_section, source="PR metadata")
            + f"\n</{boundary}>"
        )
    prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
        pr_title=redact_prompt_text(text=pr_title, source="PR title"),
        base_ref=redact_prompt_text(text=context.base_ref, source="git refs"),
        head_ref=redact_prompt_text(text=context.head_ref, source="git refs"),
        pr_summary=redact_prompt_text(text=pr_summary, source="PR metadata"),
        deferred_scope_section=deferred_section,
        external_review_section=redact_prompt_text(
            text=format_external_review_section(flags=external_flags),
            source="external reviews",
        ),
        changed_file_count=len(context.changed_files),
        changed_files=redact_prompt_text(
            text=format_changed_files_for_prompt(files=context.changed_files),
            source="changed files",
        ),
        interaction_paths=interaction_paths,
        checklist_count=len(checklist_items),
        checklist=checklist_text,
        boundary=boundary,
        diff=redact_prompt_text(text=raw_diff, source="diff"),
        lint_results_section=redact_prompt_text(
            text=format_lint_results_section(digest=lint_digest),
            source="lint results",
        ),
        strictness_section="",
        output_schema=REVIEW_OUTPUT_SCHEMA,
        output_rules=format_output_rules(checklist_count=len(checklist_items)),
    )
    return prompt, prompt_mapping
