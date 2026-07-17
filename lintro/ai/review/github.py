"""GitHub PR posting adapter for AI review results.

Renders a rich, telemetry-informative sticky comment (one per PR, updated in
place) with a severity-count header, TL;DR, per-finding blocks (severity color
emoji, category/confidence chips, collapsible cause/fix), an always-visible
cumulative telemetry header, per-run mechanics with exact vs approximate (``~``)
labeling, and a machine-readable state block. All model-derived text is
sanitized (``@mentions`` neutralized, size capped) since it comes from an
untrusted PR diff.

Public helpers live in sibling modules and are re-exported here so existing
``lintro.ai.review.github`` imports remain stable after the size-gate split
(issue #1113).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github_constants import (
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_render import (
    _format_findings_section,
    _partition_findings,
    format_finding_comment,
    format_review_summary,
    format_run_mechanics,
    sanitize_comment_text,
)
from lintro.ai.review.github_sticky import (
    _cap_body,
    build_sticky_comment,
    parse_review_state,
)
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
    "STATE_MARKER_PREFIX",
    "STICKY_MARKER",
    "MAX_COMMENT_CHARS",
    "_cap_body",
    "_format_findings_section",
    "build_sticky_comment",
    "format_error_comment",
    "format_finding_comment",
    "format_review_summary",
    "format_run_mechanics",
    "parse_review_state",
    "post_review_error_to_github",
    "post_review_to_github",
    "sanitize_comment_text",
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
    """Post (or update) the sticky review comment and inline findings.

    Maintains a single sticky comment per PR (identified by ``STICKY_MARKER``),
    updated in place with cumulative telemetry. Diff-mappable findings are also
    posted as inline review comments carrying suggestion blocks.

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
    comment_id, prior_runs = _load_prior_runs(reporter=gh_reporter)
    diff_lines = gh_reporter.fetch_pr_diff_lines()
    body = build_sticky_comment(
        result=result,
        prior_runs=prior_runs,
        checklist_display=checklist_display,
        question_map=prompt_questions,
        diff_lines=diff_lines,
    )

    success = _upsert_sticky(reporter=gh_reporter, body=body, comment_id=comment_id)

    inline_findings, _fallback = _partition_findings(
        findings=result.findings,
        diff_lines=diff_lines,
    )
    if inline_findings and not _post_inline_findings(
        reporter=gh_reporter,
        findings=inline_findings,
        checklist_display=checklist_display,
        question_map=prompt_questions,
    ):
        success = False

    return success


def post_review_error_to_github(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
) -> bool:
    """Post (or update) the sticky comment with a formatted API-error message.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
        metadata: Optional metadata for a mechanics footer.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.

    Returns:
        True when posting succeeded; False otherwise.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping error posting")
        return False
    comment_id, prior_runs = _load_prior_runs(reporter=gh_reporter)
    body = format_error_comment(
        error=error,
        provider=provider,
        metadata=metadata,
        prior_runs=prior_runs,
    )
    return _upsert_sticky(reporter=gh_reporter, body=body, comment_id=comment_id)


def _load_prior_runs(
    *,
    reporter: GitHubPRReporter,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Locate the sticky comment and parse its prior run records.

    Args:
        reporter: GitHub reporter used to list PR comments.

    Returns:
        Tuple of ``(comment_id, run_records)``; the id is ``None`` when no
        sticky comment exists yet.
    """
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    if found is None:
        return None, []
    comment_id, prior_body = found
    return comment_id, parse_review_state(body=prior_body)


def _upsert_sticky(
    *,
    reporter: GitHubPRReporter,
    body: str,
    comment_id: int | None,
) -> bool:
    """Update the sticky comment in place, or create it when absent."""
    if comment_id is not None:
        return reporter.update_issue_comment(comment_id=comment_id, body=body)
    return reporter.post_issue_comment(body)


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
