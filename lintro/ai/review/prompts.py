"""Prompt construction for AI diff review.

The review pass has two prompt shapes: the API-transport prompt, which embeds a
redacted diff, and the git-native prompt used by CLI-backed providers, which can
either embed the same redacted diff or delegate diff retrieval to the provider.
Both are byte-locked by the goldens in ``tests/unit/ai/review/golden`` — treat
any change to the emitted text as a behaviour change (issue #2301).

Redaction is a security invariant of this module: every caller-supplied string
that reaches a prompt passes through :func:`redact_prompt_text` first, and the
git-native builder embeds the redacted diff unless the caller explicitly opts
out of that guarantee.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.prompts.review import (
    REVIEW_GIT_NATIVE_DIFF_GIT_COMMAND,
    REVIEW_GIT_NATIVE_DIFF_INLINE,
    REVIEW_GIT_NATIVE_DIFF_WORKTREE_COMMAND,
    REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE,
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SYSTEM,
    REVIEW_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_lint_results_section,
    format_output_rules,
    format_pr_changed_files_for_prompt,
)
from lintro.ai.review.paths_registry import generate_interaction_paths
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.sanitize import make_boundary_marker
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.review.models.file_classification import FileClassification
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "PromptInputs",
    "build_git_native_review_prompt",
    "build_review_prompt",
    "estimate_prompt_overhead",
]

_PROMPT_OVERHEAD_TOKENS = 12_000


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptInputs:
    """Everything a review prompt renders besides the transport-specific parts.

    Both builders read the same chunk, context and checklist material; only the
    diff delivery differs between them. Grouping the shared material keeps each
    builder's signature small and makes a new prompt input a field here rather
    than another keyword threaded through every caller (issue #2301).

    Attributes:
        chunk: Semantic diff chunk to review.
        context: Full review context for PR metadata and file list.
        checklist_text: Formatted checklist for the prompt.
        checklist_count: Number of checklist items in the prompt.
        interaction_paths: Domain-triggered interaction path text.
        lint_results: Optional lint digest for prompt injection.
        extra_checklist: Additional generated checklist rows for depth 2.
        strictness_section: Sensitivity instructions for the review pass.
        max_findings: Optional per-call findings ceiling for CLI transport.
    """

    chunk: ReviewChunk
    context: ReviewContext
    checklist_text: str
    checklist_count: int
    interaction_paths: str
    lint_results: str | None = None
    extra_checklist: str = ""
    strictness_section: str = ""
    max_findings: int | None = None


def _combined_checklist(*, inputs: PromptInputs) -> tuple[str, int]:
    """Fold any generated checklist rows into the selected checklist.

    Args:
        inputs: Shared prompt material for the chunk being reviewed.

    Returns:
        Tuple of (checklist text, checklist item count).
    """
    checklist_count = inputs.checklist_count
    combined_checklist = inputs.checklist_text
    extra_checklist = inputs.extra_checklist
    if extra_checklist.strip():
        combined_checklist = f"{inputs.checklist_text}\n\n{extra_checklist.strip()}"
        checklist_count += extra_checklist.strip().count("\n") + (
            1 if extra_checklist.strip() else 0
        )
    return combined_checklist, checklist_count


def build_review_prompt(*, inputs: PromptInputs) -> tuple[str, str]:
    """Build system and user prompts for a review chunk.

    Args:
        inputs: Chunk, context and checklist material to render.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    chunk = inputs.chunk
    context = inputs.context
    max_findings = inputs.max_findings
    pr_title = context.pr_metadata.title if context.pr_metadata else "Local changes"
    pr_title = redact_prompt_text(text=pr_title, source="PR title")
    pr_summary = context.pr_metadata.body if context.pr_metadata else "(no PR summary)"
    pr_summary = redact_prompt_text(text=pr_summary, source="PR metadata")
    redacted_diff = redact_prompt_text(text=chunk.diff, source="diff")
    changed_files = [file for file in context.changed_files if file.path in chunk.files]
    combined_checklist, checklist_count = _combined_checklist(inputs=inputs)

    user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
        pr_title=pr_title,
        base_ref=redact_prompt_text(text=context.base_ref, source="git refs"),
        head_ref=redact_prompt_text(text=context.head_ref, source="git refs"),
        pr_summary=pr_summary,
        deferred_scope_section="",
        external_review_section="",
        changed_file_count=len(changed_files),
        changed_files=redact_prompt_text(
            text=format_changed_files_for_prompt(files=changed_files),
            source="changed files",
        ),
        pr_changed_files=redact_prompt_text(
            text=format_pr_changed_files_for_prompt(
                files=context.changed_files,
                chunk_paths=set(chunk.files),
            ),
            source="changed files",
        ),
        interaction_paths=inputs.interaction_paths,
        checklist_count=checklist_count,
        checklist=combined_checklist,
        boundary=make_boundary_marker(),
        diff=redacted_diff,
        lint_results_section=redact_prompt_text(
            text=format_lint_results_section(digest=inputs.lint_results),
            source="lint results",
        ),
        strictness_section=inputs.strictness_section,
        output_schema=REVIEW_OUTPUT_SCHEMA,
        output_rules=format_output_rules(
            checklist_count=checklist_count,
            max_findings=max_findings,
        ),
    )
    return REVIEW_SYSTEM, user_prompt


def build_git_native_review_prompt(
    *,
    inputs: PromptInputs,
    embed_diff: bool = False,
    allow_unredacted_git_native: bool = False,
) -> tuple[str, str]:
    """Build git-native prompts for CLI-backed review (all providers).

    Redaction is a security invariant and wins by default. When ``embed_diff``
    is False the builder would normally emit a delegated ``git diff`` command,
    which lets the provider produce the diff itself and thus bypasses lintro's
    secret-redaction choke point. Unless ``allow_unredacted_git_native`` is
    explicitly True, the builder instead falls back to embedding the redacted
    diff so no unredacted diff can reach the provider.

    Args:
        inputs: Chunk, context and checklist material to render.
        embed_diff: When True, inline the diff instead of agentic git commands.
        allow_unredacted_git_native: Explicit opt-out permitting the delegated
            ``git diff`` command path (which bypasses secret redaction) when
            ``embed_diff`` is False. Defaults to False so redaction always
            wins and the diff is embedded and redacted instead.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    # Redaction wins by default: never delegate diff retrieval to the provider
    # unless the caller has explicitly opted out of the redaction guarantee.
    if not embed_diff and not allow_unredacted_git_native:
        embed_diff = True
    chunk = inputs.chunk
    context = inputs.context
    max_findings = inputs.max_findings
    pr_title = context.pr_metadata.title if context.pr_metadata else "Local changes"
    pr_title = redact_prompt_text(text=pr_title, source="PR title")
    pr_summary = context.pr_metadata.body if context.pr_metadata else "(no PR summary)"
    pr_summary = redact_prompt_text(text=pr_summary, source="PR metadata")
    changed_files = [file for file in context.changed_files if file.path in chunk.files]
    combined_checklist, checklist_count = _combined_checklist(inputs=inputs)

    git_diff_paths = " ".join(shlex.quote(path) for path in chunk.files)
    boundary = make_boundary_marker()
    if embed_diff:
        diff_section = REVIEW_GIT_NATIVE_DIFF_INLINE.format(
            boundary=boundary,
            diff=redact_prompt_text(text=chunk.diff, source="diff"),
        )
    elif context.head_ref == "WORKTREE":
        diff_section = REVIEW_GIT_NATIVE_DIFF_WORKTREE_COMMAND.format(
            base_ref=context.base_ref,
            git_diff_paths=git_diff_paths,
        )
    else:
        diff_section = REVIEW_GIT_NATIVE_DIFF_GIT_COMMAND.format(
            base_ref=context.base_ref,
            head_ref=context.head_ref,
            git_diff_paths=git_diff_paths,
        )
    user_prompt = REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE.format(
        pr_title=pr_title,
        base_ref=redact_prompt_text(text=context.base_ref, source="git refs"),
        head_ref=redact_prompt_text(text=context.head_ref, source="git refs"),
        pr_summary=pr_summary,
        deferred_scope_section="",
        external_review_section="",
        changed_file_count=len(changed_files),
        changed_files=redact_prompt_text(
            text=format_changed_files_for_prompt(files=changed_files),
            source="changed files",
        ),
        pr_changed_files=redact_prompt_text(
            text=format_pr_changed_files_for_prompt(
                files=context.changed_files,
                chunk_paths=set(chunk.files),
            ),
            source="changed files",
        ),
        interaction_paths=inputs.interaction_paths,
        checklist_count=checklist_count,
        checklist=combined_checklist,
        boundary=boundary,
        diff_section=diff_section,
        lint_results_section=redact_prompt_text(
            text=format_lint_results_section(digest=inputs.lint_results),
            source="lint results",
        ),
        strictness_section=inputs.strictness_section,
        output_schema=REVIEW_OUTPUT_SCHEMA,
        output_rules=format_output_rules(
            checklist_count=checklist_count,
            max_findings=max_findings,
        ),
    )
    return REVIEW_SYSTEM, user_prompt


def estimate_prompt_overhead(
    *,
    context: ReviewContext,
    checklist_text: str,
    classifications: list[FileClassification],
    lint_results: str | None,
) -> int:
    """Estimate non-diff prompt token overhead.

    Args:
        context: Full review context for PR metadata and file list.
        checklist_text: Formatted checklist for the prompt.
        classifications: Per-file classifications driving interaction paths.
        lint_results: Optional lint digest for prompt injection.

    Returns:
        Estimated token count consumed by everything but the diff.
    """
    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=[file.path for file in context.changed_files],
    )
    overhead_text = "\n".join(
        [
            REVIEW_SYSTEM,
            checklist_text,
            paths,
            context.pr_metadata.body if context.pr_metadata else "",
            lint_results or "",
        ],
    )
    estimated = estimate_tokens(overhead_text)
    return int(max(estimated, _PROMPT_OVERHEAD_TOKENS))
