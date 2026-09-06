"""Carry out a comment-lifecycle decision against the GitHub API (#2305).

:mod:`lintro.ai.review.lifecycle.decision` says what to do; this module does
it, and it is the only place in the review that creates, edits, or replaces
one of the review's pull-request comments. The success path, the error path
and the converged path all reach GitHub through :func:`upsert_comment`, so a
leftover comment from another actor is handled the same way whichever surface
happens to run into it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.comment_action import CommentAction
from lintro.ai.review.enums.comment_kind import CommentKind
from lintro.ai.review.github_constants import ARCHIVE_MARKER, STICKY_MARKER
from lintro.ai.review.lifecycle.decision import (
    CommentPlan,
    ExistingComment,
    decide,
)
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.sticky import parse_sticky_state

__all__ = [
    "UpsertOutcome",
    "load_sticky_comment",
    "locate_comment",
    "upsert_archive",
    "upsert_comment",
]

#: Marker identifying each comment kind on the pull request. A kind is exactly
#: "the comment carrying this marker", which is why one lookup serves them all.
_MARKERS: dict[CommentKind, str] = {
    CommentKind.STICKY: STICKY_MARKER,
    CommentKind.ARCHIVE: ARCHIVE_MARKER,
    CommentKind.ERROR: STICKY_MARKER,
}


@dataclass(frozen=True, kw_only=True, slots=True)
class UpsertOutcome:
    """What happened when a plan was carried out.

    Attributes:
        ok: Whether the comment ended up carrying the new body.
        comment_id: The comment a later refresh must PATCH: the original id
            after an in-place edit, the replacement id after a supersede, or
            ``None`` after a first-time create (the caller re-locates it by
            marker) and after a failure.
    """

    ok: bool
    comment_id: int | None


def locate_comment(
    *,
    reporter: GitHubPRReporter,
    kind: CommentKind,
) -> ExistingComment:
    """Find the review's comment of one kind on the pull request.

    Args:
        reporter: GitHub reporter used to list the pull request's comments.
        kind: Which comment to look for.

    Returns:
        ExistingComment: The live comment, or an empty descriptor when the
        kind has not been posted yet.
    """
    found = reporter.find_issue_comment(marker=_MARKERS[kind])
    return ExistingComment(comment_id=None if found is None else found[0])


def load_sticky_comment(
    *,
    reporter: GitHubPRReporter,
) -> tuple[ExistingComment, ReviewState]:
    """Locate the sticky comment and decode any state left behind in it.

    Authoritative state lives in workflow artifacts since #2154, so the
    decoded state is only ever a fallback for a comment written by an older
    lintro. A pre-v2 blob decodes as no state at all (#2305).

    Args:
        reporter: GitHub reporter used to list the pull request's comments.

    Returns:
        tuple[ExistingComment, ReviewState]: The live sticky comment and the
        state recovered from its body, both empty when there is no sticky.
    """
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    if found is None:
        return ExistingComment(), ReviewState()
    comment_id, body = found
    return ExistingComment(comment_id=comment_id), parse_sticky_state(body=body)


def upsert_comment(
    *,
    reporter: GitHubPRReporter,
    kind: CommentKind,
    existing: ExistingComment,
    body: str,
) -> UpsertOutcome:
    """Write a comment of one kind, creating, editing or replacing it.

    GitHub only lets the creating actor PATCH a comment, and after #2050 the
    poster is ``lintro-review[bot]``, so a leftover ``github-actions[bot]``
    comment answers ``403`` and has to be superseded rather than edited. That
    answer is fed back through :func:`decide` instead of being branched on
    here, so the fallback is the same decision the caller started from.

    Args:
        reporter: GitHub reporter used to create, edit, or replace the
            comment.
        kind: Which of the review's comments is being written.
        existing: What is already on the pull request for that kind.
        body: Markdown body to write.

    Returns:
        UpsertOutcome: Whether the write landed, and the id later refreshes
        must PATCH.
    """
    plan = decide(kind=kind, existing=existing, new=body)
    if plan.action is CommentAction.CREATE:
        return UpsertOutcome(ok=reporter.post_issue_comment(plan.body), comment_id=None)
    if plan.action is CommentAction.UPDATE and plan.comment_id is not None:
        outcome = _apply_update(
            reporter=reporter,
            plan=plan,
            comment_id=plan.comment_id,
        )
        if outcome is not None:
            return outcome
        plan = decide(
            kind=kind,
            existing=replace(existing, editable=False),
            new=body,
        )
    return _apply_supersede(reporter=reporter, plan=plan)


def upsert_archive(*, reporter: GitHubPRReporter, body: str | None) -> None:
    """Write the history-archive comment when one was rendered.

    Args:
        reporter: GitHub reporter used to find and write the archive.
        body: Archive Markdown, or ``None`` when history still fits the board.
    """
    if not body:
        return
    upsert_comment(
        reporter=reporter,
        kind=CommentKind.ARCHIVE,
        existing=locate_comment(reporter=reporter, kind=CommentKind.ARCHIVE),
        body=body,
    )


def _apply_update(
    *,
    reporter: GitHubPRReporter,
    plan: CommentPlan,
    comment_id: int,
) -> UpsertOutcome | None:
    """Edit the comment in place.

    Args:
        reporter: GitHub reporter used to edit the comment.
        plan: The update plan.
        comment_id: The comment to edit, narrowed by the caller.

    Returns:
        UpsertOutcome | None: The outcome, or ``None`` when GitHub refused the
        edit with ``403`` and the caller must decide again.
    """
    status = _patch_status(reporter=reporter, comment_id=comment_id, body=plan.body)
    if status is not None and 200 <= status < 300:
        return UpsertOutcome(ok=True, comment_id=comment_id)
    if status != 403:
        logger.warning(
            "Could not edit {} comment {} (HTTP {}); leaving it in place",
            plan.kind.value,
            comment_id,
            status,
        )
        return UpsertOutcome(ok=False, comment_id=None)
    return None


def _apply_supersede(
    *,
    reporter: GitHubPRReporter,
    plan: CommentPlan,
) -> UpsertOutcome:
    """Post a replacement comment and delete the one it supersedes.

    Args:
        reporter: GitHub reporter used to post and delete.
        plan: The supersede plan.

    Returns:
        UpsertOutcome: The replacement's id when it landed. A failed delete
        leaves both comments up rather than losing the new one.
    """
    logger.warning(
        "Could not edit {} comment {}; posting a replacement "
        "before deleting it (GitHub only lets the creating actor PATCH)",
        plan.kind.value,
        plan.comment_id,
    )
    live_id = _post_with_retry(
        reporter=reporter,
        body=plan.body,
        marker=_MARKERS[plan.kind],
    )
    if live_id is None:
        return UpsertOutcome(ok=False, comment_id=None)
    if plan.comment_id is not None and not reporter.delete_issue_comment(
        comment_id=plan.comment_id,
    ):
        logger.warning(
            "Posted replacement {} comment {} but failed to delete {}; "
            "both comments may remain",
            plan.kind.value,
            live_id,
            plan.comment_id,
        )
    return UpsertOutcome(ok=True, comment_id=live_id)


def _patch_status(
    *,
    reporter: GitHubPRReporter,
    comment_id: int,
    body: str,
) -> int | None:
    """Return the PATCH status, with a bool-reporter fallback.

    Args:
        reporter: GitHub reporter used to edit the comment.
        comment_id: Existing comment id.
        body: Markdown body to write.

    Returns:
        int | None: HTTP status when the reporter exposes one. Bool-only test
        doubles map success to ``200`` and failure to ``403`` so the
        actor-mismatch path stays covered without a status method.
    """
    status_fn = getattr(reporter, "update_issue_comment_status", None)
    if callable(status_fn):
        status = status_fn(comment_id=comment_id, body=body)
        if isinstance(status, int) or status is None:
            return status
    if reporter.update_issue_comment(comment_id=comment_id, body=body):
        return 200
    return 403


def _create(*, reporter: GitHubPRReporter, body: str, marker: str) -> int | None:
    """Create a comment and return its id.

    Args:
        reporter: GitHub reporter used to post the comment.
        body: Markdown body to write.
        marker: Marker the comment carries, used to find it again when the
            reporter's create call does not answer with an id.

    Returns:
        int | None: The new comment id, or ``None`` when creation failed.
    """
    create_fn = getattr(reporter, "create_issue_comment", None)
    if callable(create_fn):
        created = create_fn(body=body)
        if isinstance(created, int) or created is None:
            return created
    if not reporter.post_issue_comment(body):
        return None
    found = reporter.find_issue_comment(marker=marker)
    return None if found is None else found[0]


def _post_with_retry(
    *,
    reporter: GitHubPRReporter,
    body: str,
    marker: str,
) -> int | None:
    """Create a comment, retrying once after a failed POST.

    Args:
        reporter: GitHub reporter used to post the comment.
        body: Markdown body to write.
        marker: Marker the comment carries.

    Returns:
        int | None: The new comment id, or ``None`` when both attempts failed.
    """
    created = _create(reporter=reporter, body=body, marker=marker)
    if created is not None:
        return created
    logger.warning("Failed to recreate the comment; retrying once")
    return _create(reporter=reporter, body=body, marker=marker)
