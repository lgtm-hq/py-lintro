"""The one comment-lifecycle owner: create, update, supersede (#2305).

``decide`` answers the same question for every comment a review owns, so the
success path and the error path cannot drift into two different answers the
way they had before epic #1974. These tests hold both halves: the pure
decision for each kind and each outcome, and the executor that carries it out
against GitHub — including the ``403`` an actor mismatch answers with, which
is fed back through ``decide`` rather than branched on separately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.comment_action import CommentAction
from lintro.ai.review.enums.comment_kind import CommentKind
from lintro.ai.review.github_constants import ARCHIVE_MARKER, STICKY_MARKER
from lintro.ai.review.lifecycle import state as lifecycle_state
from lintro.ai.review.lifecycle.comments import (
    load_sticky_comment,
    locate_comment,
    upsert_archive,
    upsert_comment,
)
from lintro.ai.review.lifecycle.decision import ExistingComment, decide
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.state_store import load_local_state, write_local_state
from lintro.ai.review.sticky import parse_sticky_state


def _raise_offline(**_kwargs: Any) -> NoReturn:
    """Stand in for a reporter that cannot be built at all.

    Args:
        **_kwargs: The pull request context the caller passed.

    Raises:
        RuntimeError: Always — no GitHub context is reachable.
    """
    msg = "no GitHub context"
    raise RuntimeError(msg)


#: Every comment kind the lifecycle owns, so a new kind cannot quietly opt out
#: of the shared decision.
KINDS = (CommentKind.STICKY, CommentKind.ARCHIVE, CommentKind.ERROR)


@dataclass
class _RecordingClient:
    """A pull request that remembers the comments written onto it.

    A stub that only counts calls says a write was attempted; this says what
    the pull request ends up carrying, which is the thing a reviewer would
    actually see.

    Attributes:
        comments: Bodies of the comments now on the pull request, in the
            order they were written.
        markers_searched: Markers the production code looked a comment up by.
        accept_posts: Whether GitHub accepts a new comment. False stands in
            for a rejected write.
    """

    comments: list[str] = field(default_factory=list)
    markers_searched: list[str] = field(default_factory=list)
    accept_posts: bool = True

    def find_issue_comment(self, *, marker: str) -> tuple[int, str] | None:
        """Look up the comment carrying a marker.

        Args:
            marker: Marker identifying the comment kind.

        Returns:
            tuple[int, str] | None: Always ``None`` — this pull request starts
            with no comments on it.
        """
        self.markers_searched.append(marker)
        return None

    def post_issue_comment(self, body: str) -> bool:
        """Record a newly posted comment.

        Args:
            body: Markdown the production code posted.

        Returns:
            bool: Whether GitHub accepted the comment.
        """
        if not self.accept_posts:
            return False
        self.comments.append(body)
        return True

    def update_issue_comment(self, *, comment_id: int, body: str) -> bool:
        """Record an edit to a comment already on the pull request.

        Args:
            comment_id: Comment the production code edited.
            body: New Markdown body.

        Returns:
            bool: Always ``True``.
        """
        self.comments[comment_id] = body
        return True

    def delete_issue_comment(self, *, comment_id: int) -> bool:
        """Remove a comment from the pull request.

        Args:
            comment_id: Comment the production code deleted.

        Returns:
            bool: Always ``True``.
        """
        del self.comments[comment_id]
        return True


def _reporter() -> MagicMock:
    """Build a reporter stub with no existing comment.

    Returns:
        MagicMock: The stub, answering success to every write.
    """
    reporter = MagicMock()
    reporter.find_issue_comment.return_value = None
    reporter.post_issue_comment.return_value = True
    reporter.update_issue_comment.return_value = True
    reporter.delete_issue_comment.return_value = True
    del reporter.create_issue_comment
    del reporter.update_issue_comment_status
    return reporter


@pytest.mark.parametrize("kind", KINDS, ids=[kind.value for kind in KINDS])
def test_absent_comment_is_created(kind: CommentKind) -> None:
    """No comment of this kind yet means one is posted."""
    plan = decide(kind=kind, existing=ExistingComment(), new="body")

    assert_that(plan.action).is_equal_to(CommentAction.CREATE)
    assert_that(plan.comment_id).is_none()
    assert_that(plan.kind).is_equal_to(kind)
    assert_that(plan.body).is_equal_to("body")


@pytest.mark.parametrize("kind", KINDS, ids=[kind.value for kind in KINDS])
def test_editable_comment_is_updated_in_place(kind: CommentKind) -> None:
    """An editable comment is edited, which is what keeps a sticky sticky."""
    plan = decide(
        kind=kind,
        existing=ExistingComment(comment_id=42),
        new="body",
    )

    assert_that(plan.action).is_equal_to(CommentAction.UPDATE)
    assert_that(plan.comment_id).is_equal_to(42)


@pytest.mark.parametrize("kind", KINDS, ids=[kind.value for kind in KINDS])
def test_uneditable_comment_is_superseded(kind: CommentKind) -> None:
    """A comment this actor may not edit is replaced, not abandoned."""
    plan = decide(
        kind=kind,
        existing=ExistingComment(comment_id=42, editable=False),
        new="body",
    )

    assert_that(plan.action).is_equal_to(CommentAction.SUPERSEDE)
    assert_that(plan.comment_id).is_equal_to(42)


def test_upsert_creates_when_the_kind_is_absent() -> None:
    """A first round posts a new comment and edits nothing."""
    reporter = _reporter()

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(),
        body="hello",
    )

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_none()
    reporter.post_issue_comment.assert_called_once_with("hello")
    reporter.update_issue_comment.assert_not_called()


def test_upsert_patches_when_the_edit_is_allowed() -> None:
    """A same-actor comment is edited in place and keeps its id."""
    reporter = _reporter()

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_equal_to(42)
    reporter.update_issue_comment.assert_called_once_with(comment_id=42, body="hello")
    reporter.post_issue_comment.assert_not_called()
    reporter.delete_issue_comment.assert_not_called()


def test_upsert_supersedes_a_comment_another_actor_owns() -> None:
    """GitHub forbids editing another actor's comment; recreate then delete."""
    reporter = _reporter()
    reporter.update_issue_comment.return_value = False
    reporter.find_issue_comment.return_value = (99, "hello")

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_equal_to(99)
    reporter.post_issue_comment.assert_called_once_with("hello")
    reporter.delete_issue_comment.assert_called_once_with(comment_id=42)
    reporter.find_issue_comment.assert_called_once_with(marker=STICKY_MARKER)


@pytest.mark.parametrize(
    ("status", "should_recreate"),
    [
        (403, True),
        (500, False),
        (429, False),
    ],
    ids=["attr=actor_mismatch", "attr=server_error", "attr=rate_limit"],
)
def test_only_a_403_supersedes(status: int, should_recreate: bool) -> None:
    """Every other failed PATCH leaves the comment where it is."""
    reporter = _reporter()
    reporter.update_issue_comment_status = MagicMock(return_value=status)
    reporter.find_issue_comment.return_value = (99, "hello")

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    if should_recreate:
        assert_that(outcome.ok).is_true()
        assert_that(outcome.comment_id).is_equal_to(99)
        reporter.delete_issue_comment.assert_called_once_with(comment_id=42)
    else:
        assert_that(outcome.ok).is_false()
        assert_that(outcome.comment_id).is_none()
        reporter.delete_issue_comment.assert_not_called()
        reporter.post_issue_comment.assert_not_called()


def test_supersede_retries_the_post_once() -> None:
    """A failed create is retried before the leftover comment is deleted."""
    reporter = _reporter()
    reporter.update_issue_comment_status = MagicMock(return_value=403)
    reporter.post_issue_comment.side_effect = [False, True]
    reporter.find_issue_comment.return_value = (99, "hello")

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_equal_to(99)
    assert_that(reporter.post_issue_comment.call_count).is_equal_to(2)


def test_supersede_keeps_the_replacement_when_the_delete_fails() -> None:
    """Losing the delete leaves two comments, never zero."""
    reporter = _reporter()
    reporter.update_issue_comment.return_value = False
    reporter.delete_issue_comment.return_value = False
    reporter.find_issue_comment.return_value = (99, "hello")

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_equal_to(99)
    reporter.delete_issue_comment.assert_called_once_with(comment_id=42)


def test_each_kind_is_located_by_its_own_marker() -> None:
    """The archive is a different comment from the board, not the same one."""
    reporter = _reporter()
    seen: list[str] = []

    def _find(*, marker: str) -> tuple[int, str] | None:
        """Record the marker looked up and answer for the archive only.

        Args:
            marker: Marker the production code searched for.

        Returns:
            tuple[int, str] | None: The archive comment, or ``None``.
        """
        seen.append(marker)
        return (7, "archive") if marker == ARCHIVE_MARKER else None

    reporter.find_issue_comment.side_effect = _find

    assert_that(
        locate_comment(reporter=reporter, kind=CommentKind.ARCHIVE).comment_id,
    ).is_equal_to(7)
    assert_that(
        locate_comment(reporter=reporter, kind=CommentKind.STICKY).comment_id,
    ).is_none()
    assert_that(seen).is_equal_to([ARCHIVE_MARKER, STICKY_MARKER])


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, []),
        ("", []),
        ("## history", ["## history"]),
    ],
    ids=["attr=none", "attr=empty", "attr=rendered"],
)
def test_the_archive_is_written_only_when_history_overflowed(
    body: str | None,
    expected: list[str],
) -> None:
    """History that still fits the board leaves the archive comment alone.

    Args:
        body: Archive Markdown the renderer produced, or ``None`` when the
            board still holds its whole history.
        expected: Comment bodies the pull request should end up carrying.
    """
    client = _RecordingClient()

    upsert_archive(reporter=client, body=body)

    assert_that(client.comments).is_equal_to(expected)
    assert_that(client.markers_searched).is_equal_to(
        [ARCHIVE_MARKER] if expected else [],
    )


def test_a_v1_only_sticky_body_is_read_as_no_prior_state() -> None:
    """A pre-v2 blob is treated as absent, so the round starts fresh (#2305).

    The v1 schema stored run aggregates with no round numbers and no finding
    identity. Migrating it meant guessing round order from list position;
    #2305 retired that guess, so the comment is still updated in place — it is
    the same board — but the history behind it is created rather than
    recovered.
    """
    reporter = _reporter()
    payload = '{"version": 1, "runs": [{"model": "m", "total": 10}]}'
    body = f"{STICKY_MARKER}\n\n<!-- lintro-ai-review-state: {payload} -->"
    reporter.find_issue_comment.return_value = (42, body)

    existing, state = load_sticky_comment(reporter=reporter)
    plan = decide(kind=CommentKind.STICKY, existing=existing, new="next round")

    assert_that(state.runs).is_empty()
    assert_that(state.next_round).is_equal_to(1)
    assert_that(plan.action).is_equal_to(CommentAction.UPDATE)


def test_a_v2_sticky_body_still_carries_its_history_forward() -> None:
    """The retirement is v1's alone: a v2 blob decodes as before."""
    reporter = _reporter()
    payload = '{"version": 2, "runs": [{"round": 1, "model": "m", "total": 10}]}'
    body = f"{STICKY_MARKER}\n\n<!-- lintro-ai-review-state: {payload} -->"
    reporter.find_issue_comment.return_value = (42, body)

    _existing, state = load_sticky_comment(reporter=reporter)

    assert_that(state.runs).is_length(1)
    assert_that(state.next_round).is_equal_to(2)


def test_a_missing_sticky_yields_an_empty_state() -> None:
    """No comment means no state, and the next write is a create."""
    reporter = _reporter()

    existing, state = load_sticky_comment(reporter=reporter)

    assert_that(existing.comment_id).is_none()
    assert_that(state.runs).is_empty()
    assert_that(
        decide(kind=CommentKind.STICKY, existing=existing, new="first").action,
    ).is_equal_to(CommentAction.CREATE)


def test_a_reporter_that_answers_with_an_id_skips_the_relocate() -> None:
    """A create call that reports its id needs no marker lookup afterwards."""
    reporter = _reporter()
    reporter.update_issue_comment.return_value = False
    reporter.create_issue_comment = MagicMock(return_value=77)

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(outcome.comment_id).is_equal_to(77)
    reporter.find_issue_comment.assert_not_called()


def test_a_bool_only_reporter_still_reaches_the_supersede_path() -> None:
    """A test double with no status method maps failure to 403 (#2050)."""
    reporter = _reporter()
    calls: list[Any] = []

    def _update(**kwargs: Any) -> bool:
        """Record the edit attempt and refuse it.

        Args:
            **kwargs: The comment id and body the production code sent.

        Returns:
            bool: Always ``False``.
        """
        calls.append(kwargs)
        return False

    reporter.update_issue_comment.side_effect = _update
    reporter.find_issue_comment.return_value = (99, "hello")

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.ERROR,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(calls).is_length(1)
    assert_that(outcome.comment_id).is_equal_to(99)


def test_an_unknown_patch_status_is_not_an_actor_mismatch() -> None:
    """A transport failure leaves the comment alone rather than replacing it."""
    reporter = _reporter()
    reporter.update_issue_comment_status = MagicMock(return_value=None)

    outcome = upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    assert_that(outcome.ok).is_false()
    assert_that(outcome.comment_id).is_none()
    reporter.delete_issue_comment.assert_not_called()
    reporter.post_issue_comment.assert_not_called()


def test_the_replacement_is_posted_before_the_original_is_deleted() -> None:
    """A failed create must leave the old comment standing, not nothing."""
    reporter = _reporter()
    reporter.update_issue_comment_status = MagicMock(return_value=403)
    reporter.find_issue_comment.return_value = (99, "hello")

    upsert_comment(
        reporter=reporter,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body="hello",
    )

    names = [call[0] for call in reporter.method_calls]
    assert_that(names.index("post_issue_comment")).is_less_than(
        names.index("delete_issue_comment"),
    )


def _v2_sticky_body(*, runs: int) -> str:
    """Render a sticky body carrying a v2 state blob with ``runs`` rounds.

    Args:
        runs: How many completed rounds the blob records.

    Returns:
        str: The comment body, marker and blob included.
    """
    payload = json.dumps(
        {
            "version": 2,
            "runs": [
                {"round": index, "model": "m", "total": 10}
                for index in range(1, runs + 1)
            ],
        },
    )
    return f"{STICKY_MARKER}\n\n<!-- lintro-ai-review-state: {payload} -->"


def test_a_posting_run_recovers_the_stickys_history_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_review_result: ReviewResult,
) -> None:
    """The board and the state written beside it describe the same history.

    Both stores are empty and the sticky comment still carries a v2 blob. The
    posting path falls back to that blob to render, so the loader has to fall
    back to it too — otherwise the round renders three recovered rounds and
    persists a fresh round 1, and the next run prefers the store it just
    wrote and loses them for good. The assertion follows the state all the way
    to the ledger the next round will read.

    Args:
        monkeypatch: Fixture used to point the ledger at a scratch directory.
        tmp_path: Scratch directory standing in for the ledger.
        sample_review_result: The round being persisted.
    """
    ledger = tmp_path / "ledger"
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path / "parts"))
    monkeypatch.setattr("lintro.ai.review.state_store.LOCAL_STATE_DIR", ledger)
    monkeypatch.setattr(
        lifecycle_state,
        "_sticky_state",
        lambda **_kwargs: parse_sticky_state(body=_v2_sticky_body(runs=3)),
    )

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
        post=True,
    )
    lifecycle_state.persist_review_state(
        result=sample_review_result,
        context=SimpleNamespace(base_ref="main", head_ref="feature"),
        prior=loaded,
        pr_number=7,
        repo="lgtm-hq/py-lintro",
    )
    persisted = load_local_state(
        key="pr-7",
        repo="lgtm-hq/py-lintro",
        pr_number=7,
        directory=ledger,
    )

    assert_that([run.round for run in loaded.runs]).is_equal_to([1, 2, 3])
    assert_that([run.round for run in persisted.runs]).is_equal_to([1, 2, 3, 4])


def test_a_run_that_posts_nothing_never_reads_the_sticky(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without ``--post`` the loader stays offline, as it always has.

    Args:
        monkeypatch: Fixture used to point the ledger at a scratch directory.
        tmp_path: Scratch directory standing in for the ledger.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        "lintro.ai.review.state_store.LOCAL_STATE_DIR",
        tmp_path / "ledger",
    )
    consulted: list[str] = []

    def _sticky(**_kwargs: Any) -> ReviewState:
        """Record that the sticky was consulted and answer with history.

        Args:
            **_kwargs: The pull request the loader asked about.

        Returns:
            ReviewState: A state that would be obvious in the result.
        """
        consulted.append("sticky")
        return parse_sticky_state(body=_v2_sticky_body(runs=3))

    monkeypatch.setattr(lifecycle_state, "_sticky_state", _sticky)

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
    )

    assert_that(consulted).is_empty()
    assert_that(loaded.runs).is_empty()


def test_a_stored_state_wins_over_the_stickys_leftover_blob(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The authoritative store is authoritative; the comment is a last resort.

    Args:
        monkeypatch: Fixture used to point the ledger at a scratch directory.
        tmp_path: Scratch directory standing in for the ledger.
    """
    ledger = tmp_path / "ledger"
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr("lintro.ai.review.state_store.LOCAL_STATE_DIR", ledger)
    write_local_state(
        state=ReviewState(
            runs=(RunRecord(round=1, sha="abc1234"),),
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
        key="pr-7",
        directory=ledger,
    )
    monkeypatch.setattr(
        lifecycle_state,
        "_sticky_state",
        lambda **_kwargs: parse_sticky_state(body=_v2_sticky_body(runs=3)),
    )

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
        post=True,
    )

    assert_that([run.round for run in loaded.runs]).is_equal_to([1])
    assert_that(loaded.runs[0].sha).is_equal_to("abc1234")


def test_an_unreadable_sticky_leaves_the_round_starting_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No token, no context, no network: a review still runs.

    Args:
        monkeypatch: Fixture used to point the ledger at a scratch directory.
        tmp_path: Scratch directory standing in for the ledger.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        "lintro.ai.review.state_store.LOCAL_STATE_DIR",
        tmp_path / "ledger",
    )
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        _raise_offline,
    )

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
        post=True,
    )

    assert_that(loaded.runs).is_empty()
    assert_that(loaded.next_round).is_equal_to(1)


def test_a_failed_archive_write_is_reported_rather_than_swallowed() -> None:
    """A dropped archive says so; it does not fail the round (#2412 review)."""
    client = _RecordingClient(accept_posts=False)

    outcome = upsert_archive(reporter=client, body="## history")

    assert_that(outcome.ok).is_false()
    assert_that(client.comments).is_empty()


def test_nothing_to_archive_is_not_a_failed_archive() -> None:
    """History that still fits reports success, not a swallowed error."""
    client = _RecordingClient()

    outcome = upsert_archive(reporter=client, body=None)

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_none()
