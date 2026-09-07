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
from lintro.ai.review.github_constants import (
    ARCHIVE_MARKER,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STICKY_MARKER,
)
from lintro.ai.review.lifecycle import comments as comments_module
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
from lintro.ai.review.state_store import (
    load_ci_state,
    load_local_state,
    write_local_state,
)
from lintro.ai.review.sticky import parse_sticky_state


def _raise_transport_error(**_kwargs: Any) -> NoReturn:
    """Stand in for a GitHub that cannot be reached at all.

    Args:
        **_kwargs: The pull request context the caller passed.

    Raises:
        OSError: Always — ``urllib``'s ``URLError`` is one of these.
    """
    msg = "connection refused"
    raise OSError(msg)


def _raise_defect(**_kwargs: Any) -> NoReturn:
    """Stand in for a bug in this package rather than a failed request.

    Args:
        **_kwargs: The pull request context the caller passed.

    Raises:
        AttributeError: Always — the shape a typo in the reader would take.
    """
    msg = "'NoneType' object has no attribute 'runs'"
    raise AttributeError(msg)


def _redirect_state_stores(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Point both state stores at scratch directories.

    Args:
        monkeypatch: Fixture used to set the environment and module globals.
        tmp_path: Scratch directory to hold the ledger and the parts.
    """
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path / "parts"))
    monkeypatch.setattr(
        "lintro.ai.review.state_store.LOCAL_STATE_DIR",
        tmp_path / "ledger",
    )


def _read_back(*, tmp_path: Path, in_actions: bool) -> ReviewState:
    """Read the state back from wherever this environment wrote it.

    Args:
        tmp_path: Scratch directory holding the ledger and the parts.
        in_actions: Whether the run was inside GitHub Actions, which writes
            artifact parts instead of a ledger entry.

    Returns:
        ReviewState: What the next round would load.
    """
    if in_actions:
        return load_ci_state(
            directory=tmp_path / "parts",
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        )
    return load_local_state(
        key="pr-7",
        repo="lgtm-hq/py-lintro",
        pr_number=7,
        directory=tmp_path / "ledger",
    )


def _sticky_body_with_state(*, payload: dict[str, Any]) -> str:
    """Render a sticky comment body carrying a leftover state blob.

    Built from the marker constants rather than transcribed, so a change to
    either marker fails the decoder tests instead of silently making these
    fixtures undecodable.

    Args:
        payload: The state blob to embed.

    Returns:
        str: The comment body.
    """
    blob = json.dumps(payload)
    return f"{STICKY_MARKER}\n\n{STATE_MARKER_PREFIX} {blob} {STATE_MARKER_SUFFIX}"


#: Every comment kind the lifecycle owns, derived from the enum rather than
#: listed, so a new kind is parametrised into these tests the moment it exists
#: instead of quietly opting out of the shared decision.
KINDS: tuple[CommentKind, ...] = tuple(CommentKind)


@dataclass
class _RecordingClient:
    """A pull request that remembers the comments written onto it.

    A stub that only counts calls says a write was attempted; this says what
    the pull request ends up carrying, which is the thing a reviewer would
    actually see.

    Attributes:
        comments: Bodies of the comments now on the pull request, keyed by the
            id GitHub would have given them.
        markers_searched: Markers the production code looked a comment up by.
        accept_posts: Whether GitHub accepts a new comment. False stands in
            for a rejected write.
        patch_status: HTTP status the next edit answers with.
    """

    comments: dict[int, str] = field(default_factory=dict)
    markers_searched: list[str] = field(default_factory=list)
    accept_posts: bool = True
    patch_status: int = 200

    @property
    def bodies(self) -> list[str]:
        """Return the comment bodies in the order they were written.

        Returns:
            list[str]: What the pull request now carries.
        """
        return [self.comments[key] for key in sorted(self.comments)]

    def find_issue_comment(self, *, marker: str) -> tuple[int, str] | None:
        """Look up the comment carrying a marker.

        Args:
            marker: Marker identifying the comment kind.

        Returns:
            tuple[int, str] | None: The comment's id and body, or ``None``.
            Searching what was actually posted is what lets a second write to
            the same kind take the update path rather than posting twice.
        """
        self.markers_searched.append(marker)
        for comment_id in sorted(self.comments):
            if marker in self.comments[comment_id]:
                return comment_id, self.comments[comment_id]
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
        self.comments[max(self.comments, default=0) + 1] = body
        return True

    def update_issue_comment(self, *, comment_id: int, body: str) -> bool:
        """Record an edit to a comment already on the pull request.

        Args:
            comment_id: Comment the production code edited.
            body: New Markdown body.

        Returns:
            bool: Whether the edit took effect.
        """
        return (
            200
            <= self.update_issue_comment_status(
                comment_id=comment_id,
                body=body,
            )
            < 300
        )

    def update_issue_comment_status(self, *, comment_id: int, body: str) -> int:
        """Edit a comment the way the production reporter does, with a status.

        Args:
            comment_id: Comment the production code edited.
            body: New Markdown body.

        Returns:
            int: The status GitHub would have answered with.
        """
        if 200 <= self.patch_status < 300:
            self.comments[comment_id] = body
        return self.patch_status

    def delete_issue_comment(self, *, comment_id: int) -> bool:
        """Remove a comment from the pull request.

        Args:
            comment_id: Comment the production code deleted.

        Returns:
            bool: Always ``True``.
        """
        del self.comments[comment_id]
        return True


def _reporter(*, patch_status: int = 200) -> MagicMock:
    """Build a reporter stub shaped like the production reporter.

    ``GitHubPRReporter`` always answers a PATCH with its HTTP status, so that
    is the default here too: a stub that could only answer ``True``/``False``
    would put most of these tests on the compatibility path rather than the
    one production takes.

    Args:
        patch_status: Status an edit answers with. ``403`` is the actor
            mismatch that supersedes; anything else outside 2xx is a plain
            failure.

    Returns:
        MagicMock: The stub, with no existing comment on the pull request.
    """
    reporter = MagicMock()
    reporter.find_issue_comment.return_value = None
    reporter.post_issue_comment.return_value = True
    reporter.update_issue_comment.return_value = 200 <= patch_status < 300
    reporter.update_issue_comment_status.return_value = patch_status
    reporter.delete_issue_comment.return_value = True
    del reporter.create_issue_comment
    return reporter


def _bool_only_reporter() -> MagicMock:
    """Build a client that answers a PATCH with a bool and nothing else.

    The status method is on the ``CommentClient`` protocol, but a ``Mock`` is
    never checked against a protocol, so this shape is still reachable — and
    it is the shape #2050's actor-mismatch handling was written against.

    Returns:
        MagicMock: The stub, whose failed edits read as an actor mismatch.
    """
    reporter = _reporter()
    reporter.update_issue_comment.return_value = False
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
    reporter.update_issue_comment_status.assert_called_once_with(
        comment_id=42,
        body="hello",
    )
    reporter.post_issue_comment.assert_not_called()
    reporter.delete_issue_comment.assert_not_called()


def test_upsert_supersedes_a_comment_another_actor_owns() -> None:
    """GitHub forbids editing another actor's comment; recreate then delete."""
    reporter = _reporter(patch_status=403)
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
    reporter = _reporter(patch_status=status)
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
    reporter = _reporter(patch_status=403)
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
    reporter = _reporter(patch_status=403)
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

    assert_that(client.bodies).is_equal_to(expected)
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
    body = _sticky_body_with_state(payload={"version": 1, "runs": [{"model": "m"}]})
    reporter.find_issue_comment.return_value = (42, body)

    existing, state = load_sticky_comment(reporter=reporter)
    plan = decide(kind=CommentKind.STICKY, existing=existing, new="next round")

    assert_that(state.runs).is_empty()
    assert_that(state.next_round).is_equal_to(1)
    assert_that(plan.action).is_equal_to(CommentAction.UPDATE)


def test_a_v2_sticky_body_still_carries_its_history_forward() -> None:
    """The retirement is v1's alone: a v2 blob decodes as before."""
    reporter = _reporter()
    body = _sticky_body_with_state(
        payload={"version": 2, "runs": [{"round": 1, "model": "m"}]},
    )
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
    reporter = _reporter(patch_status=403)
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
    reporter = _bool_only_reporter()
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
    reporter.update_issue_comment_status.return_value = None

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
    reporter = _reporter(patch_status=403)
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
    return _sticky_body_with_state(
        payload={
            "version": 2,
            "runs": [
                {"round": index, "model": "m", "total": 10}
                for index in range(1, runs + 1)
            ],
        },
    )


def test_a_failed_archive_write_is_reported_rather_than_swallowed() -> None:
    """A dropped archive says so; it does not fail the round (#2412 review)."""
    client = _RecordingClient(accept_posts=False)

    outcome = upsert_archive(reporter=client, body="## history")

    assert_that(outcome.ok).is_false()
    assert_that(client.bodies).is_empty()


def test_nothing_to_archive_is_not_a_failed_archive() -> None:
    """History that still fits reports success, not a swallowed error."""
    client = _RecordingClient()

    outcome = upsert_archive(reporter=client, body=None)

    assert_that(outcome.ok).is_true()
    assert_that(outcome.comment_id).is_none()


def test_the_archive_is_edited_in_place_on_a_later_round() -> None:
    """A second overflow round edits the archive; it does not post a twin."""
    client = _RecordingClient()

    first = upsert_archive(reporter=client, body=f"{ARCHIVE_MARKER}\nrounds 1-3")
    second = upsert_archive(reporter=client, body=f"{ARCHIVE_MARKER}\nrounds 1-6")

    assert_that(first.comment_id).is_none()
    assert_that(second.comment_id).is_not_none()
    assert_that(client.bodies).is_equal_to([f"{ARCHIVE_MARKER}\nrounds 1-6"])


def test_every_comment_kind_has_a_marker() -> None:
    """A new kind cannot ship without saying which comment it is.

    ``locate_comment`` and the supersede relocate both index the marker map
    directly, so a kind missing from it is a ``KeyError`` at write time — in
    the middle of posting a review, after the board has already been
    rendered.
    """
    assert_that(set(comments_module._MARKERS)).is_equal_to(set(CommentKind))


def test_the_error_surface_shares_the_boards_marker() -> None:
    """A failed round is a state of the board, not a comment of its own.

    ``format_error_comment`` embeds ``STICKY_MARKER`` in the body it renders,
    so a superseded failure comment has to be re-located by that same marker.
    """
    assert_that(comments_module._MARKERS[CommentKind.ERROR]).is_equal_to(
        comments_module._MARKERS[CommentKind.STICKY],
    )


@pytest.mark.parametrize("kind", KINDS, ids=[kind.value for kind in KINDS])
def test_a_supersede_relocates_by_the_superseded_kinds_marker(
    kind: CommentKind,
) -> None:
    """The replacement is found again by the marker the body actually carries.

    Args:
        kind: The comment kind being superseded.
    """
    marker = comments_module._MARKERS[kind]
    reporter = _reporter(patch_status=403)
    reporter.find_issue_comment.return_value = (99, marker)

    outcome = upsert_comment(
        reporter=reporter,
        kind=kind,
        existing=ExistingComment(comment_id=42),
        body=marker,
    )

    assert_that(outcome.comment_id).is_equal_to(99)
    reporter.find_issue_comment.assert_called_once_with(marker=marker)


def test_a_replacement_that_cannot_be_posted_leaves_the_original_alone() -> None:
    """Both creates refused means the old comment stays: never zero comments.

    The retry exists so a flaky POST does not cost the board. If it also
    fails there is nothing to replace the original with, and deleting it
    anyway would leave the pull request with no review comment at all.
    """
    client = _RecordingClient(accept_posts=False)
    client.comments[42] = f"{STICKY_MARKER}\nthe old board"
    client.patch_status = 403

    outcome = upsert_comment(
        reporter=client,
        kind=CommentKind.STICKY,
        existing=ExistingComment(comment_id=42),
        body=f"{STICKY_MARKER}\nthe new board",
    )

    assert_that(outcome.ok).is_false()
    assert_that(outcome.comment_id).is_none()
    assert_that(client.bodies).is_equal_to([f"{STICKY_MARKER}\nthe old board"])


def test_a_sticky_blob_never_seeds_coverage() -> None:
    """A comment cannot green the coverage gate (#2154's trust boundary).

    ``migrate_legacy_sticky`` used to strip coverage explicitly; #2305 deleted
    it, so the guarantee now rests on ``parse_sticky_state`` being the blob
    decoder rather than the artifact parser. A blob carrying a coverage array
    — which nothing writes, and a forged comment could — must still load as no
    coverage at all, or a pull request's own comment could declare itself
    fully reviewed.
    """
    body = _sticky_body_with_state(
        payload={
            "version": 2,
            "runs": [{"round": 1, "model": "m"}],
            "coverage": [{"path": "src/main.py", "patch_hash": "h"}],
            "flagged_files": [{"path": "src/other.py", "patch_hash": "h2"}],
        },
    )

    state = parse_sticky_state(body=body)

    assert_that(state.runs).is_length(1)
    assert_that(state.coverage).is_empty()
    assert_that(state.flagged_files).is_empty()


def _sticky_reporter(*, body: str | None) -> MagicMock:
    """Build a reporter answering with one sticky comment, or none.

    Args:
        body: The sticky comment's body, or ``None`` for a pull request that
            has never been commented on.

    Returns:
        MagicMock: The reporter stub.
    """
    reporter = MagicMock()
    reporter.find_issue_comment.return_value = None if body is None else (42, body)
    return reporter


@pytest.mark.parametrize(
    "in_actions",
    [False, True],
    ids=["attr=local", "attr=ci"],
)
def test_a_posting_run_recovers_the_stickys_history_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_review_result: ReviewResult,
    in_actions: bool,
) -> None:
    """The board and the state written beside it describe the same history.

    Both stores are empty and the sticky comment still carries a v2 blob. The
    posting path falls back to that blob to render, so the loader has to fall
    back to it too — otherwise the round renders three recovered rounds and
    persists a fresh round 1, and the next round prefers the store it just
    wrote and loses them for good.

    Parametrised over the environment because the workflow is the case that
    actually posts: the CI short-circuit reads artifacts instead of the
    ledger, and used to return before the sticky was ever consulted. The
    assertion follows the state to the store the next round reads.

    Args:
        monkeypatch: Fixture used to redirect both state stores.
        tmp_path: Scratch directory standing in for them.
        sample_review_result: The round being persisted.
        in_actions: Whether the run is inside GitHub Actions.
    """
    _redirect_state_stores(monkeypatch=monkeypatch, tmp_path=tmp_path)
    (
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        if in_actions
        else monkeypatch.delenv(
            "GITHUB_ACTIONS",
            raising=False,
        )
    )
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        lambda **_kwargs: _sticky_reporter(body=_v2_sticky_body(runs=3)),
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
    persisted = _read_back(tmp_path=tmp_path, in_actions=in_actions)

    assert_that([run.round for run in loaded.runs]).is_equal_to([1, 2, 3])
    assert_that([run.round for run in persisted.runs]).is_equal_to([1, 2, 3, 4])


def test_a_run_that_posts_nothing_never_reads_the_sticky(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without ``--post`` the loader stays offline, as it always has.

    Args:
        monkeypatch: Fixture used to redirect both state stores.
        tmp_path: Scratch directory standing in for them.
    """
    _redirect_state_stores(monkeypatch=monkeypatch, tmp_path=tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    built: list[str] = []

    def _reporter_factory(**_kwargs: Any) -> MagicMock:
        """Record that a reporter was built at all.

        Args:
            **_kwargs: The pull request the loader asked about.

        Returns:
            MagicMock: A reporter carrying recoverable history.
        """
        built.append("reporter")
        return _sticky_reporter(body=_v2_sticky_body(runs=3))

    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        _reporter_factory,
    )

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
    )

    assert_that(built).is_empty()
    assert_that(loaded.runs).is_empty()


def test_a_stored_state_wins_over_the_stickys_leftover_blob(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The authoritative store is authoritative; the comment is a last resort.

    Args:
        monkeypatch: Fixture used to redirect both state stores.
        tmp_path: Scratch directory standing in for them.
    """
    _redirect_state_stores(monkeypatch=monkeypatch, tmp_path=tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    write_local_state(
        state=ReviewState(
            runs=(RunRecord(round=1, sha="abc1234"),),
            repo="lgtm-hq/py-lintro",
            pr_number=7,
        ),
        key="pr-7",
        directory=tmp_path / "ledger",
    )
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        lambda **_kwargs: _sticky_reporter(body=_v2_sticky_body(runs=3)),
    )

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
        post=True,
    )

    assert_that([run.round for run in loaded.runs]).is_equal_to([1])
    assert_that(loaded.runs[0].sha).is_equal_to("abc1234")


def test_an_unreachable_github_leaves_the_round_starting_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No token, no context, no network: a review still runs.

    Args:
        monkeypatch: Fixture used to redirect both state stores.
        tmp_path: Scratch directory standing in for them.
    """
    _redirect_state_stores(monkeypatch=monkeypatch, tmp_path=tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        _raise_transport_error,
    )

    loaded = lifecycle_state.load_prior_review_state(
        pr_number=7,
        head_ref="feature",
        repo="lgtm-hq/py-lintro",
        post=True,
    )

    assert_that(loaded.runs).is_empty()
    assert_that(loaded.next_round).is_equal_to(1)


def test_a_defect_reading_the_sticky_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only transport and parse failures degrade; a bug still surfaces.

    The fallback used to catch every ``Exception``, which would have turned a
    broken decoder into a pull request whose history silently restarted.

    Args:
        monkeypatch: Fixture used to redirect both state stores.
        tmp_path: Scratch directory standing in for them.
    """
    _redirect_state_stores(monkeypatch=monkeypatch, tmp_path=tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        "lintro.ai.integrations.github_pr.GitHubPRReporter",
        _raise_defect,
    )

    with pytest.raises(AttributeError):
        lifecycle_state.load_prior_review_state(
            pr_number=7,
            head_ref="feature",
            repo="lgtm-hq/py-lintro",
            post=True,
        )
