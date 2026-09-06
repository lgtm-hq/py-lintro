"""The two surfaces that stamp a round which produced no findings (#1954).

A round can end without a board of its own: the provider failed, or the stop
rule short-circuited it before it ran. Neither may blank the mission-control
board a reviewer is working from, so both re-render the last good board from
persisted state and write a one-line banner under the header. Both reach
GitHub through the same lifecycle owner as the success path, so a leftover
comment from another actor is handled identically whichever one runs.
"""

from __future__ import annotations

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.comment_kind import CommentKind
from lintro.ai.review.finding_matcher import count_blocking_findings
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_notes import format_convergence_banner
from lintro.ai.review.lifecycle.comments import load_sticky_comment, upsert_comment
from lintro.ai.review.lifecycle.state import resolve_prior_state
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.sticky import render_state_sticky

__all__ = [
    "post_review_converged_to_github",
    "post_review_error_to_github",
]


def post_review_error_to_github(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    prior_state: ReviewState | None = None,
) -> bool:
    """Post (or update) the sticky comment with a formatted API-error message.

    When the sticky already carries a successful round, the failure is
    rendered as a banner over a re-render of that round's board rather than
    replacing it (#1954). The persisted state is passed through untouched
    either way, so a failed round never advances the round counter or edits
    tracked findings.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
        metadata: Optional metadata for a mechanics footer.
        pr_number: Optional pull request number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        prior_state: Artifact or local ledger state already loaded for this
            invocation. When empty, the state left on the sticky is used.

    Returns:
        True when posting succeeded; False otherwise.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping error posting")
        return False
    existing, sticky_state = load_sticky_comment(reporter=gh_reporter)
    state = resolve_prior_state(
        prior_state=prior_state,
        sticky_state=sticky_state,
    )
    body = format_error_comment(
        error=error,
        provider=provider,
        metadata=metadata,
        prior_state=state,
        repo=repo or gh_reporter.repo or "",
        pr_number=pr_number if pr_number is not None else gh_reporter.pr_number,
    )
    return upsert_comment(
        reporter=gh_reporter,
        kind=CommentKind.ERROR,
        existing=existing,
        body=body,
    ).ok


def post_review_converged_to_github(
    *,
    decision: ConvergenceDecision,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    prior_state: ReviewState | None = None,
) -> bool:
    """Stamp the sticky comment for a round the stop rule short-circuited.

    The board itself is re-rendered untouched from persisted state and the
    convergence banner is written under the header, exactly as a failed round
    is rendered (#1954): a skipped round produced no findings, so it must not
    advance the round counter, edit tracked findings, or blank the board a
    reviewer is still working from.

    Args:
        decision: The converged decision that skipped the round.
        pr_number: Optional pull request number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        prior_state: State already loaded for this invocation. When empty, the
            sticky's own decoded state is used instead.

    Returns:
        True when posting succeeded; False when there is no pull request
        context or no recoverable prior state to re-render the board from.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping converged stamp")
        return False
    existing, sticky_state = load_sticky_comment(reporter=gh_reporter)
    state = resolve_prior_state(
        prior_state=prior_state,
        sticky_state=sticky_state,
    )
    if not state.runs:
        # Nothing recoverable to re-render: overwriting the live board with
        # the empty-state page would erase the findings a reviewer is still
        # working from, so leave the sticky untouched and say so.
        logger.warning(
            "No prior review state is recoverable — leaving the sticky "
            "untouched instead of stamping a converged round over it",
        )
        return False
    body = render_state_sticky(
        state=state,
        banner=format_convergence_banner(
            decision=decision,
            open_p1=count_blocking_findings(findings=state.findings),
        ),
        repo=repo or gh_reporter.repo or "",
        pr_number=pr_number if pr_number is not None else gh_reporter.pr_number,
    )
    return upsert_comment(
        reporter=gh_reporter,
        kind=CommentKind.STICKY,
        existing=existing,
        body=body,
    ).ok
