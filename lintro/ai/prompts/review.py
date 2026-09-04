"""Prompt templates for AI diff-based code review.

Prompt bodies are loaded verbatim from packaged template files under
``lintro/ai/prompts/templates/review``. The ``format_*_for_prompt`` helpers
remain here as Python; only the static prompt copy lives in template files.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from lintro.ai.prompts._loader import load_prompt_template
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.verdict import VERDICT_LABELS

if TYPE_CHECKING:
    from lintro.ai.review.models.chunk_summary import ChunkSummary
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "CHUNK_FILE_MARKER",
    "REVIEW_ADVERSARIAL_SWEEP_TEMPLATE",
    "REVIEW_CUSTOM_AGENT_OUTPUT_SCHEMA",
    "REVIEW_CUSTOM_AGENT_SYSTEM",
    "REVIEW_CUSTOM_AGENT_USER_PROMPT_TEMPLATE",
    "REVIEW_GENERATE_QUESTIONS_TEMPLATE",
    "REVIEW_GIT_NATIVE_DIFF_GIT_COMMAND",
    "REVIEW_GIT_NATIVE_DIFF_INLINE",
    "REVIEW_GIT_NATIVE_DIFF_WORKTREE_COMMAND",
    "REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE",
    "REVIEW_OUTPUT_RULES_TEMPLATE",
    "REVIEW_OUTPUT_SCHEMA",
    "REVIEW_SCHEMA_REMINDER_TEMPLATE",
    "REVIEW_SYNTHESIS_SYSTEM_PROMPT",
    "REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE",
    "REVIEW_SYSTEM",
    "REVIEW_USER_PROMPT_TEMPLATE",
    "format_changed_files_for_prompt",
    "format_chunk_summaries_for_prompt",
    "format_checklist_table_for_prompt",
    "format_deferred_scope_section",
    "format_external_review_section",
    "format_lint_results_section",
    "format_output_rules",
    "format_pr_changed_files_for_prompt",
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

REVIEW_OUTPUT_RULES_TEMPLATE = load_prompt_template("review", "output_rules.md")

REVIEW_GENERATE_QUESTIONS_TEMPLATE = load_prompt_template(
    "review",
    "generate_questions.md",
)

REVIEW_ADVERSARIAL_SWEEP_TEMPLATE = load_prompt_template(
    "review",
    "adversarial_sweep.md",
)

REVIEW_SYNTHESIS_SYSTEM_PROMPT = load_prompt_template(
    "review",
    "synthesis_system.md",
)

REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE = load_prompt_template(
    "review",
    "synthesis_user.md",
)

REVIEW_SCHEMA_REMINDER_TEMPLATE = load_prompt_template(
    "review",
    "schema_reminder.md",
)

REVIEW_CUSTOM_AGENT_SYSTEM = load_prompt_template(
    "review",
    "custom_agent_system.md",
)

REVIEW_CUSTOM_AGENT_USER_PROMPT_TEMPLATE = load_prompt_template(
    "review",
    "custom_agent_user.md",
)

REVIEW_CUSTOM_AGENT_OUTPUT_SCHEMA = load_prompt_template(
    "review",
    "custom_agent_output_schema.json",
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


#: Suffix marking a file that is part of the chunk currently under review.
CHUNK_FILE_MARKER = "— **(this chunk)**"


def _changed_file_line(*, file: ChangedFile) -> str:
    """Render one changed file as a prompt bullet.

    Args:
        file: Changed file from review context.

    Returns:
        Bullet line with path, status, and line counts.
    """
    return f"- `{file.path}` ({file.status}, +{file.additions}/-{file.deletions})"


def format_changed_files_for_prompt(*, files: list[ChangedFile]) -> str:
    """Format changed files as a bullet list with status.

    Args:
        files: Changed files from review context.

    Returns:
        Bullet list suitable for prompt injection.
    """
    if not files:
        return "- (no changed files)"
    return "\n".join(_changed_file_line(file=file) for file in files)


def format_pr_changed_files_for_prompt(
    *,
    files: list[ChangedFile],
    chunk_paths: Collection[str],
) -> str:
    """Format the whole PR's changed files, marking the current chunk's own.

    Every chunk prompt carries the full list so a chunk can never conclude that
    a file this pull request changed was left untouched (issue #2265). Files
    outside ``chunk_paths`` are listed unmarked; the prompt template explains
    that their on-disk copies are stale base-commit versions.

    Args:
        files: All changed files for the pull request.
        chunk_paths: Paths belonging to the chunk under review.

    Returns:
        Bullet list suitable for prompt injection.
    """
    if not files:
        return "- (no changed files)"
    return "\n".join(
        (
            f"{_changed_file_line(file=file)} {CHUNK_FILE_MARKER}"
            if file.path in chunk_paths
            else _changed_file_line(file=file)
        )
        for file in files
    )


def _chunk_summary_finding_line(*, finding: ReviewFinding) -> str:
    """Render one already-reported finding as a digest line.

    Carries every location the finding covers, not only its primary one: the
    digest is what the "do not restate anything already reported" rule keys
    off, so a secondary occurrence left out of the line is a location the
    synthesis pass is free to report again.

    Args:
        finding: A finding one chunk already reported.

    Returns:
        One indented digest line — severity, primary location, any further
        locations, and the title. Never the finding's prose.
    """
    primary = FindingOccurrence(file=finding.file, line=finding.line).label
    locations = [
        occurrence.label
        for occurrence in finding.all_occurrences
        if occurrence.label != primary
    ]
    also = f" (also {', '.join(locations)})" if locations else ""
    return f"  - already reported: {finding.severity} {primary}{also} — {finding.title}"


def format_chunk_summaries_for_prompt(*, summaries: Sequence[ChunkSummary]) -> str:
    """Format the per-chunk digest the cross-chunk synthesis pass reasons over.

    One block per chunk: the files that chunk reviewed, and one line per
    finding it already reported. The finding lines exist so the pass can be
    told not to restate them, so they carry only what makes a finding
    recognizable — severity, every location, title — and never its prose.

    Args:
        summaries: Per-chunk digests in chunk order.

    Returns:
        A plain-text block suitable for prompt injection, or a sentinel line
        when no chunk produced a digest.
    """
    if not summaries:
        return "- (no chunk summaries)"
    blocks: list[str] = []
    for summary in summaries:
        files = ", ".join(f"`{path}`" for path in summary.files) or "(no files)"
        lines = [f"Piece {summary.chunk_id} reviewed: {files}"]
        if summary.findings:
            lines.extend(
                _chunk_summary_finding_line(finding=finding)
                for finding in summary.findings
            )
        else:
            lines.append("  - already reported: (nothing)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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


def _findings_cap_rule(*, max_findings: int | None) -> str:
    """Render the findings-cap bullet for the output rules block.

    Args:
        max_findings: Optional per-call findings ceiling. ``None`` means no
            hard cap (API transport).

    Returns:
        Markdown bullet(s) covering the findings cap and de-duplication rule.
    """
    if max_findings is None:
        cap_line = (
            "- There is no hard cap on findings, but **do not report the same "
            "problem twice**."
        )
    else:
        cap_line = (
            f"- Cap `findings` at **{max_findings}** for this call (questions "
            "still capped at 3). Prefer the highest-severity issues; summarize "
            "any overflow in one walkthrough bullet. Never emit truncated or "
            "mid-object JSON.\n"
            "- **Do not report the same problem twice**."
        )
    return (
        f"{cap_line} When one problem repeats across locations, report it once "
        "and list every location in `occurrences` as `file`/`line` pairs — "
        "including the primary one. It renders as a single collapsed thread, "
        "and its fix prompt enumerates every location."
    )


def format_output_rules(
    *,
    checklist_count: int,
    max_findings: int | None = None,
) -> str:
    """Render the shared output rules block for a review prompt.

    Both the diff-embedded and git-native review prompts require the exact
    same output rules; the block lives in one template so the two prompts can
    never drift apart.

    Args:
        checklist_count: Number of checklist items the model must answer.
        max_findings: Optional per-call findings ceiling (CLI transport).

    Returns:
        The rendered rules block.
    """
    return REVIEW_OUTPUT_RULES_TEMPLATE.format(
        checklist_count=checklist_count,
        label_blocked=VERDICT_LABELS[ReviewVerdict.BLOCKED],
        label_changes_requested=VERDICT_LABELS[ReviewVerdict.CHANGES_REQUESTED],
        label_nits_only=VERDICT_LABELS[ReviewVerdict.NITS_ONLY],
        label_ready=VERDICT_LABELS[ReviewVerdict.READY],
        findings_cap_rule=_findings_cap_rule(max_findings=max_findings),
    ).strip()
