"""GitHub PR posting adapter for AI review results."""

from __future__ import annotations

from typing import Any

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
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
) -> bool:
    """Post review findings as GitHub PR comments.

    Args:
        result: Review result to post.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.

    Returns:
        True when posting succeeded; False on failure or when GitHub context is
        unavailable.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping review posting")
        return False

    summary_body = format_review_summary(result=result)
    inline_findings, fallback_findings = _partition_findings(
        result=result,
        reporter=gh_reporter,
    )

    success = True
    if summary_body and not gh_reporter._post_issue_comment(summary_body):
        success = False

    if inline_findings and not _post_inline_findings(
        reporter=gh_reporter,
        findings=inline_findings,
    ):
        success = False

    for finding in fallback_findings:
        body = format_finding_comment(finding=finding)
        location = _format_fallback_location(finding=finding)
        comment = f"{location}\n\n{body}" if location else body
        if not gh_reporter._post_issue_comment(comment):
            success = False

    return success


def format_finding_comment(*, finding: ReviewFinding) -> str:
    """Format a review finding as a GitHub markdown comment.

    Args:
        finding: Review finding to format.

    Returns:
        Markdown comment body.
    """
    return (
        f"**{finding.severity}** | {finding.category} | "
        f"{finding.confidence} confidence\n\n"
        f"### {finding.title}\n\n"
        f"{finding.description}\n\n"
        f"**Cause:** {finding.cause}\n\n"
        f"**Fix:** {finding.fix}"
    )


def format_review_summary(*, result: ReviewResult) -> str:
    """Format the top-level review summary comment.

    Args:
        result: Review result to summarize.

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
            f"**Checklist:** {metadata.checklist_items} items"
        ),
        "",
        "### Summary",
        result.summary or "(no summary)",
    ]

    if result.checklist:
        lines.extend(
            ["", "### Checklist", "| ID | Answer | Evidence |", "|---|---|---|"],
        )
        for answer in result.checklist:
            evidence = (
                answer.evidence.replace("|", "\\|")
                .replace("\n", " ")
                .replace("\r", " ")
            )
            lines.append(f"| {answer.id} | {answer.answer} | {evidence} |")

    return "\n".join(lines)


def _format_fallback_location(*, finding: ReviewFinding) -> str:
    """Format the location header for a fallback issue comment."""
    if not finding.file:
        return ""
    if finding.line > 0:
        return f"`{finding.file}:{finding.line}`"
    return f"`{finding.file}`"


def _partition_findings(
    *,
    result: ReviewResult,
    reporter: GitHubPRReporter,
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    """Split findings into inline-capable and fallback groups."""
    diff_lines = reporter._fetch_pr_diff_lines()
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
) -> bool:
    """Post inline PR review comments for mappable findings."""
    comments: list[dict[str, Any]] = []
    for finding in findings:
        rel = finding.file.removeprefix("./").replace("\\", "/")
        comments.append(
            {
                "path": rel,
                "body": format_finding_comment(finding=finding),
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
    return reporter._api_request("POST", url, payload)
