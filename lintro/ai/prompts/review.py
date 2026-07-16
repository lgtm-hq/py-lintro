"""Prompt templates for AI diff-based code review.

Prompt bodies are loaded verbatim from packaged template files under
``lintro/ai/prompts/templates/review``. The ``format_*_for_prompt`` helpers
remain here as Python; only the static prompt copy lives in template files.
"""

from __future__ import annotations

from lintro.ai.prompts._loader import load_prompt_template
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem

__all__ = [
    "REVIEW_ADVERSARIAL_SWEEP_TEMPLATE",
    "REVIEW_GENERATE_QUESTIONS_TEMPLATE",
    "REVIEW_GIT_NATIVE_DIFF_GIT_COMMAND",
    "REVIEW_GIT_NATIVE_DIFF_INLINE",
    "REVIEW_GIT_NATIVE_DIFF_WORKTREE_COMMAND",
    "REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE",
    "REVIEW_OUTPUT_SCHEMA",
    "REVIEW_SYSTEM",
    "REVIEW_USER_PROMPT_TEMPLATE",
    "format_changed_files_for_prompt",
    "format_checklist_table_for_prompt",
    "format_deferred_scope_section",
    "format_external_review_section",
    "format_lint_results_section",
]

REVIEW_SYSTEM = load_prompt_template("review", "system.md")

REVIEW_USER_PROMPT_TEMPLATE = load_prompt_template("review", "user.md")

REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE = load_prompt_template(
    "review",
    "git_native_user.md",
)

REVIEW_GIT_NATIVE_DIFF_INLINE = load_prompt_template(
    "review",
    "git_native_diff_inline.md",
)

REVIEW_GIT_NATIVE_DIFF_GIT_COMMAND = load_prompt_template(
    "review",
    "git_native_diff_git_command.md",
)

REVIEW_GIT_NATIVE_DIFF_WORKTREE_COMMAND = load_prompt_template(
    "review",
    "git_native_diff_worktree_command.md",
)

REVIEW_OUTPUT_SCHEMA = load_prompt_template("review", "output_schema.json")

REVIEW_GENERATE_QUESTIONS_TEMPLATE = load_prompt_template(
    "review",
    "generate_questions.md",
)

REVIEW_ADVERSARIAL_SWEEP_TEMPLATE = load_prompt_template(
    "review",
    "adversarial_sweep.md",
)


def format_checklist_table_for_prompt(*, items: list[ChecklistItem]) -> str:
    """Format checklist items as a numbered markdown table.

    Args:
        items: Selected checklist items sorted by id.

    Returns:
        Markdown table with prompt row numbers and questions.
    """
    lines = [
        "| # | Category | Question |",
        "|---|----------|----------|",
    ]
    for item in items:
        lines.append(
            f"| {item.id} | {item.category.value} | {item.question} |",
        )
    return "\n".join(lines)


def format_changed_files_for_prompt(*, files: list[ChangedFile]) -> str:
    """Format changed files as a bullet list with status.

    Args:
        files: Changed files from review context.

    Returns:
        Bullet list suitable for prompt injection.
    """
    if not files:
        return "- (no changed files)"
    return "\n".join(
        f"- `{file.path}` ({file.status}, +{file.additions}/-{file.deletions})"
        for file in files
    )


def format_deferred_scope_section(*, text: str | None) -> str:
    """Format optional deferred scope block for the review prompt.

    Args:
        text: Deferred scope description from PR summary, if any.

    Returns:
        Markdown block or empty string when no deferred scope.
    """
    if not text or not text.strip():
        return ""
    return f"**Deferred:** {text.strip()}"


def format_external_review_section(*, flags: list[str] | None) -> str:
    """Format optional external review tool flags section.

    Args:
        flags: External tool flags to verify against current code.

    Returns:
        Markdown block or empty string when no flags provided.
    """
    if not flags:
        return ""
    joined = ", ".join(flags)
    return f"**External tools flagged:** {joined} — verify against current code."


def format_lint_results_section(*, digest: str | None) -> str:
    """Format lint digest for prompt injection.

    Args:
        digest: Compact lint results digest, if any.

    Returns:
        XML-wrapped digest or empty string when no lint results.
    """
    if not digest or not digest.strip():
        return ""
    return f"<lint_results>\n{digest.strip()}\n</lint_results>"
