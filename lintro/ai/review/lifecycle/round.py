"""One round's pass over the inline threads a review already owns (#2305).

Two things need the pull request's inline comments, and both happen after the
round's findings have been posted: the threads this round settled have to be
stamped, and the comments this round created have to be recognized so a later
round can find them again. The listing is fetched once and shared — the bodies
are what the banner is applied to, and the hidden markers are what identify a
freshly posted comment.

This is the success path's half of the lifecycle. Deciding *which* stamp a
settled thread earns is :mod:`lintro.ai.review.lifecycle.threads`.
"""

from __future__ import annotations

from collections.abc import Mapping

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.lifecycle.banners import regression_provenance
from lintro.ai.review.lifecycle.markers import (
    inline_comment_url,
    parse_finding_marker,
)
from lintro.ai.review.lifecycle.threads import sync_addressed_lifecycle
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.lifecycle_sync_request import LifecycleSyncRequest
from lintro.ai.review.models.review_state import ReviewState

__all__ = ["comment_url", "regression_notes", "run_thread_lifecycle"]


def comment_url(*, reporter: GitHubPRReporter, comment_id: int | None) -> str:
    """Build the browser URL of an inline review comment.

    Args:
        reporter: GitHub reporter carrying repo and pull request context.
        comment_id: Review comment id, or ``None`` when it is unknown.

    Returns:
        The comment's anchor URL, or an empty string — a pointer renders
        unlinked rather than as a dead link.
    """
    return inline_comment_url(
        repo=reporter.repo or "",
        pr_number=reporter.pr_number,
        comment_id=comment_id,
    )


def regression_notes(
    *,
    reporter: GitHubPRReporter,
    match: FindingMatchResult,
) -> dict[str, str]:
    """Build the provenance note each regression's fresh comment carries.

    Args:
        reporter: GitHub reporter used to link back to the original thread.
        match: This round's matching outcome.

    Returns:
        Finding key to the note. Regressions are re-raised on a new thread
        (state D), so without this a reader would be told a finding that was
        raised and fixed two rounds ago is simply new.
    """
    return {
        record.key: regression_provenance(
            record=record,
            thread_url=comment_url(
                reporter=reporter,
                comment_id=record.inline_comment_id,
            ),
        )
        for record in match.regressed
    }


def _partial_progress(
    *,
    match: FindingMatchResult,
    prior_state: ReviewState,
) -> list[FindingRecord]:
    """Select collapsed patterns that lost occurrences this round (#1925).

    Args:
        match: This round's matching outcome.
        prior_state: State carried into this round.

    Returns:
        Open records whose addressed-occurrence count rose this round. A
        pattern that merely stayed where it was is excluded, so a long-lived
        finding is not re-stamped with the same banner every round.
    """
    before = {record.key: record for record in prior_state.findings}
    progressed: list[FindingRecord] = []
    for record in match.records:
        if record.status is not FindingStatus.OPEN:
            continue
        if record.inline_comment_id is None or record.occurrences_addressed <= 0:
            continue
        previous = before.get(record.key)
        if previous is not None and previous.occurrences_addressed >= (
            record.occurrences_addressed
        ):
            continue
        progressed.append(record)
    return progressed


def _fresh_thread_id(
    *,
    record: FindingRecord,
    newest: Mapping[str, int],
) -> int | None:
    """Return the comment id of a regression's *new* thread, when there is one.

    Args:
        record: The regressed record, still pointing at its old thread.
        newest: Highest comment id seen per finding key.

    Returns:
        The new comment's id, or ``None`` when this round posted no fresh
        comment for the finding — the old thread carries the same marker, so
        without this check its banner would link to itself.
    """
    candidate = newest.get(record.key)
    if candidate is None or candidate == record.inline_comment_id:
        return None
    return candidate


def _index_comments(
    *,
    comments: list[dict[str, object]],
) -> tuple[dict[int, str], dict[str, int]]:
    """Index the pull request's inline comments by id and by finding key.

    Args:
        comments: Raw comment payloads as the API returned them.

    Returns:
        tuple[dict[int, str], dict[str, int]]: Bodies keyed by comment id, and
        the highest comment id seen per finding key.
    """
    bodies: dict[int, str] = {}
    newest: dict[str, int] = {}
    for comment in comments:
        comment_id = comment.get("id")
        body = comment.get("body")
        if not isinstance(comment_id, int) or not isinstance(body, str):
            continue
        bodies[comment_id] = body
        key = parse_finding_marker(body=body)
        if key:
            newest[key] = max(newest.get(key, 0), comment_id)
    return bodies, newest


def run_thread_lifecycle(
    *,
    reporter: GitHubPRReporter,
    match: FindingMatchResult,
    prior_state: ReviewState,
    head_sha: str,
    round_number: int,
    auto_resolve: bool,
    capture_ids: bool,
) -> dict[str, int]:
    """Stamp settled threads and capture the ids of this round's comments.

    Args:
        reporter: GitHub reporter used for the listing, edits, and mutations.
        match: This round's matching outcome.
        prior_state: State carried into this round.
        head_sha: Head commit sha reviewed in this round.
        round_number: 1-based round number for this run.
        auto_resolve: Whether an addressed thread may also be resolved.
        capture_ids: Whether inline comments were posted this round, and so
            whether there are new ids to look for.

    Returns:
        Finding key to inline comment id for records that did not already have
        one (and for regressions, whose live thread is now the new one). Empty
        when there was nothing to capture or the listing failed.
    """
    partial = _partial_progress(match=match, prior_state=prior_state)
    regressed = tuple(
        record for record in match.regressed if record.inline_comment_id is not None
    )
    resolved = tuple(
        record for record in match.resolved if record.inline_comment_id is not None
    )
    if not capture_ids and not (partial or regressed or resolved):
        return {}

    comments = reporter.fetch_review_comments()
    if not isinstance(comments, list):
        logger.debug(
            "Could not list inline review comments — lifecycle banners and "
            "comment-id capture are skipped this round",
        )
        return {}

    bodies, newest = _index_comments(comments=comments)
    sync_addressed_lifecycle(
        reporter=reporter,
        request=LifecycleSyncRequest(
            resolved=resolved,
            partial=partial,
            regressed=regressed,
            comment_bodies=bodies,
            head_sha=head_sha,
            round_number=round_number,
            auto_resolve=auto_resolve,
            new_thread_urls={
                record.key: comment_url(
                    reporter=reporter,
                    comment_id=_fresh_thread_id(record=record, newest=newest),
                )
                for record in regressed
            },
        ),
    )

    if not capture_ids:
        return {}
    # A record that already has a thread keeps it: that is the comment the
    # banners are written onto. A regression is the exception — its live thread
    # is the fresh one, because the old thread stays resolved.
    regressed_keys = {record.key for record in match.regressed}
    return {
        record.key: newest[record.key]
        for record in match.records
        if record.key in newest
        and (record.inline_comment_id is None or record.key in regressed_keys)
    }
