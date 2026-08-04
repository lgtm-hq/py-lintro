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

from typing import Any, Protocol

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_render import (
    _format_findings_section as _format_findings_section,
)
from lintro.ai.review.github_render import (
    _partition_findings,
    format_finding_comment,
    format_review_summary,
    format_run_mechanics,
    sanitize_comment_text,
)
from lintro.ai.review.github_sticky import (
    _cap_body as _cap_body,
)
from lintro.ai.review.github_sticky import (
    build_sticky_comment,
    parse_review_state,
    parse_review_state_v2,
)
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "GITHUB_COMMENT_HARD_LIMIT",
    "STATE_MARKER_PREFIX",
    "STICKY_MARKER",
    "MAX_COMMENT_CHARS",
    "build_sticky_comment",
    "format_error_comment",
    "format_finding_comment",
    "format_review_summary",
    "format_run_mechanics",
    "parse_review_state",
    "parse_review_state_v2",
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
    comment_id, prior_state = _load_prior_state(reporter=gh_reporter)
    diff_lines = gh_reporter.fetch_pr_diff_lines()

    def render(*, inline_failure: InlinePostFailure | None) -> str:
        """Render the sticky body against the unchanged prior state."""
        return build_sticky_comment(
            result=result,
            prior_state=prior_state,
            head_sha=result.metadata.head_ref,
            checklist_display=checklist_display,
            question_map=prompt_questions,
            diff_lines=diff_lines,
            inline_failure=inline_failure,
        )

    inline_findings, fallback = _partition_findings(
        findings=result.findings,
        diff_lines=diff_lines,
    )

    # A finding that maps to no line in the diff never gets an inline comment,
    # so the sticky is its only surface from the outset — fold its detail in
    # rather than leaving it as a title in a table (#1909). The sticky is also
    # posted before the inline comments so an inline failure still leaves a
    # status comment on the PR.
    success = _upsert_sticky(
        reporter=gh_reporter,
        body=render(
            inline_failure=_inline_failure(unmappable=fallback, rejected=[]),
        ),
        comment_id=comment_id,
    )

    if inline_findings and not _post_inline_findings(
        reporter=gh_reporter,
        findings=inline_findings,
        checklist_display=checklist_display,
        question_map=prompt_questions,
    ):
        success = False
        # Degraded path (#1909): the rejected findings now have no surface
        # either, so re-render with both groups folded in. ``prior_state`` is
        # unchanged, so this round is not double-counted, and the comment is
        # only ever *updated* — a failed lookup skips rather than posting a
        # second sticky on the PR.
        _fold_inline_failure_into_sticky(
            reporter=gh_reporter,
            render=render,
            failure=_inline_failure(
                unmappable=fallback,
                rejected=inline_findings,
            ),
            comment_id=comment_id,
        )

    return success


def _inline_failure(
    *,
    unmappable: list[ReviewFinding],
    rejected: list[ReviewFinding],
) -> InlinePostFailure | None:
    """Describe the findings that have no inline comment to live on.

    Two different things put a finding here, and the reason says which: it
    anchors to no line in the PR's diff, or GitHub rejected the review batch
    that carried it.

    Args:
        unmappable: Findings that map to no line in the diff.
        rejected: Findings whose inline review batch the API rejected.

    Returns:
        The failure descriptor, or ``None`` when every finding has an inline
        surface.
    """
    findings = [*rejected, *unmappable]
    if not findings:
        return None
    reasons = []
    if rejected:
        # ``_post_inline_findings`` only reports a boolean, so a 422, a 5xx, a
        # timeout and a network error all arrive here identically. The wording
        # must not name a cause the code never observed.
        reasons.append("the inline review comments could not be posted")
    if unmappable:
        reasons.append("some findings map to no line in this PR's diff")
    return InlinePostFailure(reason="; ".join(reasons), findings=tuple(findings))


class _StickyRenderer(Protocol):
    """Callable that renders the sticky body for a given inline-post outcome."""

    def __call__(self, *, inline_failure: InlinePostFailure | None) -> str:
        """Render the body.

        Args:
            inline_failure: Findings whose inline comments could not be posted.

        Returns:
            The complete sticky comment body.
        """
        ...  # pragma: no cover - structural type only


def _fold_inline_failure_into_sticky(
    *,
    reporter: GitHubPRReporter,
    render: _StickyRenderer,
    failure: InlinePostFailure | None,
    comment_id: int | None,
) -> None:
    """Re-render the sticky with the unpostable findings' detail folded in.

    Failing here is not worth failing the run over — the caller has already
    recorded the inline failure — but it must not be silent either, or a PR
    quietly ends up with a verdict whose findings appear nowhere.

    Args:
        reporter: GitHub reporter used to locate and update the sticky comment.
        render: Callable that renders the sticky body for a given failure.
        failure: Findings whose inline comments could not be posted.
        comment_id: Sticky comment id known before the upsert, or ``None``.
    """
    if failure is None:
        return
    sticky_id = _sticky_comment_id(reporter=reporter, known=comment_id)
    if sticky_id is None:
        logger.warning(
            "Sticky comment not found — inline-post failure details could not "
            "be folded into it",
        )
        return
    if not reporter.update_issue_comment(
        comment_id=sticky_id,
        body=render(inline_failure=failure),
    ):
        logger.warning(
            "Failed to fold inline-post failure details into the sticky comment",
        )


def _sticky_comment_id(
    *,
    reporter: GitHubPRReporter,
    known: int | None,
) -> int | None:
    """Return the sticky comment's id, re-locating it when it was just created.

    Args:
        reporter: GitHub reporter used to list PR comments.
        known: The id known before the sticky was upserted, or ``None`` when it
            did not exist yet.

    Returns:
        The comment id to update, or ``None`` when it still cannot be found.
    """
    if known is not None:
        return known
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    return None if found is None else found[0]


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
    comment_id, prior_state = _load_prior_state(reporter=gh_reporter)
    body = format_error_comment(
        error=error,
        provider=provider,
        metadata=metadata,
        prior_state=prior_state,
    )
    return _upsert_sticky(reporter=gh_reporter, body=body, comment_id=comment_id)


def _load_prior_state(
    *,
    reporter: GitHubPRReporter,
) -> tuple[int | None, ReviewState]:
    """Locate the sticky comment and decode its persisted review state.

    Args:
        reporter: GitHub reporter used to list PR comments.

    Returns:
        Tuple of ``(comment_id, state)``; the id is ``None`` and the state is
        empty when no sticky comment exists yet.
    """
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    if found is None:
        return None, ReviewState()
    comment_id, prior_body = found
    return comment_id, parse_review_state_v2(body=prior_body)


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
