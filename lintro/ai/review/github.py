"""The success path: one round's findings posted to a pull request.

Renders the sticky mission-control board, posts the round's diff-mappable
findings as inline comments under a review body, and hands the pull request's
threads to the lifecycle owner so what this round settled is stamped and what
it created is recognized next time.

The surfaces this orchestrates live in sibling modules and are re-exported
here, so ``lintro.ai.review.github`` stays the one import a caller needs for
"post a review to GitHub":

* :mod:`lintro.ai.review.github_inline` — the inline review submission.
* :mod:`lintro.ai.review.github_status` — the failure and converged stamps.
* :mod:`lintro.ai.review.lifecycle` — create vs update vs supersede, and the
  inline-thread pass.
* :mod:`lintro.ai.review.sticky` — the board itself.
"""

from __future__ import annotations

from typing import Protocol

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.comment_kind import CommentKind
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_constants import (
    ARCHIVE_MARKER,
    GITHUB_COMMENT_HARD_LIMIT,
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_inline import (
    describe_inline_failure,
    post_inline_findings,
    record_keys,
    round_diff_lines,
)
from lintro.ai.review.github_notes import format_run_mechanics
from lintro.ai.review.github_render import (
    _partition_findings,
    format_finding_comment,
    sanitize_comment_text,
)
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.github_status import (
    post_review_converged_to_github,
    post_review_error_to_github,
)
from lintro.ai.review.lifecycle.comments import (
    load_sticky_comment,
    locate_comment,
    upsert_archive,
    upsert_comment,
)
from lintro.ai.review.lifecycle.decision import ExistingComment
from lintro.ai.review.lifecycle.round import regression_notes, run_thread_lifecycle
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.inline_post_request import InlinePostRequest
from lintro.ai.review.models.inline_post_result import InlinePostResult
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_post_options import ReviewPostOptions
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.output import render_inline_post_failure_json
from lintro.ai.review.sticky import (
    build_sticky_bodies,
    build_sticky_comment,
    matcher_reviewed_paths,
    parse_sticky_state,
)

__all__ = [
    "ARCHIVE_MARKER",
    "GITHUB_COMMENT_HARD_LIMIT",
    "MAX_COMMENT_CHARS",
    "STATE_MARKER_PREFIX",
    "STICKY_MARKER",
    "ReviewPostOptions",
    "build_review_body",
    "build_sticky_comment",
    "format_error_comment",
    "format_finding_comment",
    "format_run_mechanics",
    "parse_sticky_state",
    "post_review_converged_to_github",
    "post_review_error_to_github",
    "post_review_to_github",
    "sanitize_comment_text",
]


class _StickyRenderer(Protocol):
    """Callable that renders the sticky body for a given inline-post outcome."""

    def __call__(
        self,
        *,
        inline_failure: InlinePostFailure | None,
        comment_ids: dict[str, int] | None = None,
    ) -> str:
        """Render the body.

        Args:
            inline_failure: Findings whose inline comments could not be posted.
            comment_ids: Finding key to inline comment id, persisted so a later
                round can edit those comments.

        Returns:
            The complete sticky comment body.
        """
        ...  # pragma: no cover - structural type only


def post_review_to_github(
    *,
    result: ReviewResult,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    options: ReviewPostOptions | None = None,
) -> bool:
    """Post (or update) the sticky review comment and inline findings.

    Maintains a single sticky comment per pull request (identified by
    ``STICKY_MARKER``), updated in place with cumulative telemetry.
    Diff-mappable findings are also posted as inline review comments, under a
    review body describing this round (#1910). Threads whose finding this
    round settled are stamped with their outcome and, when configuration
    allows, resolved (#1912).

    Args:
        result: Review result to post.
        pr_number: Optional pull request number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        options: What this round renders and what the lifecycle may do.
            Defaults are a first round with no carried state.

    Returns:
        True when posting succeeded; False on failure or when GitHub context
        is unavailable.
    """
    settings = options or ReviewPostOptions()
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping review posting")
        return False

    existing, sticky_state = load_sticky_comment(reporter=gh_reporter)
    prior_state = _resolve_prior_state(
        prior_state=settings.prior_state,
        sticky_state=sticky_state,
    )
    diff_lines = gh_reporter.fetch_pr_diff_lines()
    head_sha = result.metadata.head_ref

    def render(
        *,
        inline_failure: InlinePostFailure | None,
        comment_ids: dict[str, int] | None = None,
    ) -> str:
        """Render the primary sticky body against the unchanged prior state.

        Args:
            inline_failure: Findings whose inline comments could not be posted.
            comment_ids: Finding key to inline comment id captured this round.

        Returns:
            The primary sticky body. The archive body, when history no longer
            fits, is stashed on the callable for the caller to write.
        """
        primary, archive = build_sticky_bodies(
            request=StickyRequest(
                result=result,
                prior_state=prior_state,
                head_sha=head_sha,
                checklist_display=settings.checklist_display,
                question_map=settings.question_map or {},
                diff_lines=diff_lines,
                transport=settings.transport,
                auth_mode=settings.auth_mode,
                cost_basis=settings.cost_basis,
                inline_failure=inline_failure,
                inline_comment_ids=comment_ids,
                repo=gh_reporter.repo or "",
                pr_number=gh_reporter.pr_number,
                departed_paths=settings.departed_paths,
            ),
        )
        render.archive = archive  # type: ignore[attr-defined]
        return primary

    render.archive = None  # type: ignore[attr-defined]

    inline_findings, fallback = _partition_findings(
        findings=result.findings,
        diff_lines=diff_lines,
    )
    # Matching is pure and deterministic over (prior_state, findings), so the
    # sticky, the review body and the inline comments all recompute the same
    # transitions and cannot disagree about what is new or carried over.
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha=head_sha,
        reviewed_paths=matcher_reviewed_paths(result=result),
        departed_paths=settings.departed_paths,
    )

    # A finding that maps to no line in the diff never gets an inline comment,
    # so the sticky is its only surface from the outset — fold its detail in
    # rather than leaving it as a title in a table (#1909). The sticky is also
    # posted before the inline comments so an inline failure still leaves a
    # status comment on the pull request.
    failure = describe_inline_failure(
        unmappable=fallback,
        rejected=[],
        outcome=None,
    )
    # "Nothing to post" is not a failure, but it is also not a posted comment:
    # id capture has nothing to look for, while the lifecycle pass still runs
    # — a round that fixed everything posts no inline comment and yet has the
    # most threads to stamp.
    inline_posted = False
    outcome = upsert_comment(
        reporter=gh_reporter,
        kind=CommentKind.STICKY,
        existing=existing,
        body=render(inline_failure=failure),
    )
    success = outcome.ok
    upsert_archive(reporter=gh_reporter, body=getattr(render, "archive", None))

    if inline_findings:
        posted = _post_round_findings(
            reporter=gh_reporter,
            result=result,
            prior_state=prior_state,
            match=match,
            findings=inline_findings,
            settings=settings,
            diff_lines=diff_lines,
        )
        inline_posted = posted.ok
        if not inline_posted:
            success = False
            # Degraded path (#1909): the rejected findings now have no surface
            # either, so the sticky is re-rendered below with both groups
            # folded in. ``prior_state`` is unchanged, so this round is not
            # double-counted.
            failure = describe_inline_failure(
                unmappable=fallback,
                rejected=inline_findings,
                outcome=posted,
            )
            if failure is not None:
                # The CI classifier reads this envelope out of the captured
                # log to report a sticky-only round honestly, instead of
                # claiming the findings were posted inline (#2266).
                logger.warning(
                    "Inline review comments were not posted; this round's "
                    "findings reached the sticky comment only: {}",
                    render_inline_post_failure_json(failure=failure),
                )

    comment_ids = run_thread_lifecycle(
        reporter=gh_reporter,
        match=match,
        prior_state=prior_state,
        head_sha=head_sha,
        round_number=prior_state.next_round,
        auto_resolve=settings.auto_resolve,
        capture_ids=inline_posted,
    )

    # One follow-up edit at most: it carries the newly captured comment ids
    # (so a later round can find these threads) and, when inline posting
    # failed, the folded-in detail for findings that now have no surface.
    if comment_ids or (inline_findings and not inline_posted):
        _refresh_sticky(
            reporter=gh_reporter,
            render=render,
            failure=failure,
            comment_ids=comment_ids,
            comment_id=outcome.comment_id,
        )
        upsert_archive(reporter=gh_reporter, body=getattr(render, "archive", None))

    if settings.captured_comment_ids is not None and comment_ids:
        settings.captured_comment_ids.update(comment_ids)
    return success


def _resolve_prior_state(
    *,
    prior_state: ReviewState | None,
    sticky_state: ReviewState,
) -> ReviewState:
    """Pick the state this round continues from.

    Args:
        prior_state: State already loaded for this invocation, if any.
        sticky_state: State recovered from the sticky comment's own body.

    Returns:
        ReviewState: The loaded state when it carries anything, otherwise
        whatever the comment itself still holds.
    """
    if prior_state is None or not (
        prior_state.coverage or prior_state.runs or prior_state.findings
    ):
        return sticky_state
    return prior_state


def _post_round_findings(
    *,
    reporter: GitHubPRReporter,
    result: ReviewResult,
    prior_state: ReviewState,
    match: FindingMatchResult,
    findings: list[ReviewFinding],
    settings: ReviewPostOptions,
    diff_lines: dict[str, set[int]] | None,
) -> InlinePostResult:
    """Submit this round's inline comments under a freshly built review body.

    Args:
        reporter: GitHub reporter used to submit the review.
        result: Review result to post.
        prior_state: State carried into this round.
        match: This round's matching outcome.
        findings: Diff-mappable findings to anchor.
        settings: What this round renders.
        diff_lines: The pull request's cumulative diff lines.

    Returns:
        InlinePostResult: The submission outcome.
    """
    # The body is self-contained — it carries its own fix prompt inline
    # (#1956) — so it needs nothing from the sticky upsert and the ordering is
    # driven only by the inline-failure fallback.
    review_body = build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha=result.metadata.head_ref,
        transport=settings.transport,
        auth_mode=settings.auth_mode,
        config_source=settings.config_source,
        new_commits=_count_new_commits(reporter=reporter, prior_state=prior_state),
    )
    return post_inline_findings(
        reporter=reporter,
        request=InlinePostRequest(
            findings=findings,
            checklist_display=settings.checklist_display,
            question_map=settings.question_map or {},
            review_body=review_body,
            round_diff_lines=round_diff_lines(
                reporter=reporter,
                prior_state=prior_state,
                diff_lines=diff_lines,
                head_sha=result.metadata.head_ref,
            ),
            carried_fingerprints=frozenset(
                record.fingerprint for record in match.carried
            ),
            finding_keys=record_keys(findings=findings, match=match),
            provenance=regression_notes(reporter=reporter, match=match),
        ),
    )


def _refresh_sticky(
    *,
    reporter: GitHubPRReporter,
    render: _StickyRenderer,
    failure: InlinePostFailure | None,
    comment_ids: dict[str, int],
    comment_id: int | None,
) -> None:
    """Re-render the sticky after this round's inline comments are known.

    Two things can only be written once the inline posting has been attempted:
    the ids of the comments it created (#1912) and, when it failed, the folded
    detail of findings that now have no surface (#1909). Failing here is not
    worth failing the run over — the caller has already recorded any inline
    failure — but it must not be silent either, or a pull request quietly ends
    up with a verdict whose findings appear nowhere, or with threads no later
    round can find.

    Args:
        reporter: GitHub reporter used to locate and update the sticky comment.
        render: Callable that renders the sticky body.
        failure: Findings whose inline comments could not be posted.
        comment_ids: Finding key to inline comment id captured this round.
        comment_id: Live sticky comment id after the upsert, or ``None`` when
            the comment was just created and must be re-located.
    """
    existing = (
        ExistingComment(comment_id=comment_id)
        if comment_id is not None
        else locate_comment(reporter=reporter, kind=CommentKind.STICKY)
    )
    if existing.comment_id is None:
        logger.warning(
            "Sticky comment not found — inline comment ids and any inline-post "
            "failure details could not be written to it",
        )
        return
    if not reporter.update_issue_comment(
        comment_id=existing.comment_id,
        body=render(inline_failure=failure, comment_ids=comment_ids),
    ):
        logger.warning("Failed to refresh the sticky comment after inline posting")


def _count_new_commits(
    *,
    reporter: GitHubPRReporter,
    prior_state: ReviewState,
) -> int | None:
    """Count commits pushed since the previously reviewed head.

    Args:
        reporter: GitHub reporter used to list the pull request's commits.
        prior_state: State carried into this round.

    Returns:
        Number of commits after the previous round's head sha, or ``None``
        when there is no previous round or the sha is not in the listing.
    """
    if not prior_state.runs:
        return None
    prior_sha = prior_state.runs[-1].sha
    if not prior_sha:
        return None
    shas = reporter.fetch_pr_commit_shas()
    if not shas:
        return None
    for index, sha in enumerate(shas):
        if sha.startswith(prior_sha) or prior_sha.startswith(sha):
            return len(shas) - index - 1
    return None
