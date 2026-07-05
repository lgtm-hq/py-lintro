"""GitHub PR posting adapter for AI review results."""

from __future__ import annotations

from typing import Any

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.checklist_display import (
    cleared_answers,
    format_review_questions_markdown,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
    "format_finding_comment",
    "format_review_summary",
    "post_review_to_github",
]


def post_review_to_github(
    *,
    result: ReviewResult,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> bool:
    """Post review findings as GitHub PR comments.

    Args:
        result: Review result to post.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.

    Returns:
        True when posting succeeded; False on failure or when GitHub context is
        unavailable.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping review posting")
        return False

    prompt_questions = question_map or {}
    summary_body = format_review_summary(
        result=result,
        checklist_display=checklist_display,
        question_map=prompt_questions,
    )
    inline_findings, fallback_findings = _partition_findings(
        result=result,
        reporter=gh_reporter,
    )

    success = True
    if summary_body and not gh_reporter.post_issue_comment(summary_body):
        success = False

    if inline_findings and not _post_inline_findings(
        reporter=gh_reporter,
        findings=inline_findings,
        checklist_display=checklist_display,
        question_map=prompt_questions,
    ):
        success = False

    for finding in fallback_findings:
        body = format_finding_comment(
            finding=finding,
            checklist_display=checklist_display,
            question_map=prompt_questions,
        )
        location = _format_fallback_location(finding=finding)
        comment = f"{location}\n\n{body}" if location else body
        if not gh_reporter.post_issue_comment(comment):
            success = False

    return success


def format_finding_comment(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> str:
    """Format a review finding as a GitHub markdown comment.

    Args:
        finding: Review finding to format.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.

    Returns:
        Markdown comment body.
    """
    prompt_questions = question_map or {}
    body = (
        f"**{finding.severity}** | {finding.category} | "
        f"{finding.confidence} confidence\n\n"
        f"### {finding.title}\n\n"
        f"{finding.description}\n\n"
        f"**Cause:** {finding.cause}\n\n"
        f"**Fix:** {finding.fix}"
    )
    if checklist_display in {ChecklistDisplay.LINKED, ChecklistDisplay.ALL}:
        linked = questions_for_finding(
            finding=finding,
            question_map=prompt_questions,
        )
        body += format_review_questions_markdown(questions=linked)
    return body


def format_review_summary(
    *,
    result: ReviewResult,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> str:
    """Format the top-level review summary comment.

    Args:
        result: Review result to summarize.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text (unused except for appendix).

    Returns:
        Markdown summary comment body.
    """
    metadata = result.metadata
    lines = [
        "## Lintro Review",
        "",
        (
            f"**Model:** {metadata.model} | **Depth:** {metadata.depth} | "
            f"**Files:** {metadata.files_reviewed}/{metadata.files_total} | "
            f"**Structured checks:** {metadata.checklist_items}"
        ),
        "",
        "### Summary",
        result.summary or "(no summary)",
    ]

    outside_diff = [
        finding for finding in result.findings if not finding.file or finding.line <= 0
    ]
    if outside_diff:
        lines.extend(["", "### Findings outside diff"])
        for finding in outside_diff:
            lines.append(f"- **{finding.severity}** {finding.title} ({finding.file})")

    if checklist_display == ChecklistDisplay.ALL:
        lines.extend(_format_checklist_appendix_markdown(result=result))

    return "\n".join(lines)


def _format_fallback_location(*, finding: ReviewFinding) -> str:
    """Format the location header for a fallback issue comment."""
    if not finding.file:
        return ""
    if finding.line > 0:
        return f"`{finding.file}:{finding.line}`"
    return f"`{finding.file}`"


def _format_checklist_appendix_markdown(*, result: ReviewResult) -> list[str]:
    """Build cleared/orphan checklist appendix lines for markdown."""
    cleared = cleared_answers(answers=result.checklist)
    orphans = orphan_concerns(
        answers=result.checklist,
        findings=result.findings,
    )
    lines = ["", f"### Cleared checks ({len(cleared)})"]
    if cleared:
        for answer in cleared:
            question = answer.question or f"(checklist item {answer.id})"
            lines.append(f"- ✓ {question}")
    else:
        lines.append("- (none)")

    lines.extend(["", f"### Checklist concerns without findings ({len(orphans)})"])
    if orphans:
        for answer in orphans:
            question = answer.question or f"(checklist item {answer.id})"
            evidence = answer.evidence.replace("|", "\\|")
            lines.append(f"- {question}")
            if evidence.strip():
                truncated = evidence if len(evidence) <= 120 else f"{evidence[:117]}..."
                lines.append(f"  - {truncated}")
    else:
        lines.append("- (none — good)")
    return lines


def _partition_findings(
    *,
    result: ReviewResult,
    reporter: GitHubPRReporter,
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    """Split findings into inline-capable and fallback groups."""
    diff_lines = reporter.fetch_pr_diff_lines()
    inline: list[ReviewFinding] = []
    fallback: list[ReviewFinding] = []

    for finding in result.findings:
        rel = finding.file.removeprefix("./").replace("\\", "/")
        if (
            not rel
            or finding.line <= 0
            or diff_lines is None
            or finding.line not in diff_lines.get(rel, set())
        ):
            fallback.append(finding)
        else:
            inline.append(finding)

    return inline, fallback


def _post_inline_findings(
    *,
    reporter: GitHubPRReporter,
    findings: list[ReviewFinding],
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
) -> bool:
    """Post inline PR review comments for mappable findings."""
    comments: list[dict[str, Any]] = []
    for finding in findings:
        rel = finding.file.removeprefix("./").replace("\\", "/")
        comments.append(
            {
                "path": rel,
                "body": format_finding_comment(
                    finding=finding,
                    checklist_display=checklist_display,
                    question_map=question_map,
                ),
                "line": finding.line,
                "side": "RIGHT",
            },
        )

    if not comments:
        return True

    payload = {
        "event": "COMMENT",
        "body": "Lintro review findings",
        "comments": comments,
    }
    url = (
        f"{reporter.api_base}/repos/{reporter.repo}/pulls/"
        f"{reporter.pr_number}/reviews"
    )
    return reporter.api_request("POST", url, payload)
