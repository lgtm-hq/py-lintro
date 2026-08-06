"""Tests for the addressed lifecycle on inline finding threads (#1912)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.lifecycle_stage import LifecycleStage
from lintro.ai.review.finding_matcher import fingerprint_for
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_constants import (
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STICKY_MARKER,
)
from lintro.ai.review.github_lifecycle import (
    LIFECYCLE_OPEN,
    apply_lifecycle_block,
    finding_marker,
    parse_finding_marker,
    regression_provenance,
    render_lifecycle_block,
    sync_addressed_lifecycle,
)
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.review_thread import ReviewThread
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.review_state_codec import render_state_block

_TEST_TOKEN = (
    "ghp_test_fixture_token"  # noqa: S105  # nosec B105 — fake test fixture token
)
_HEAD_SHA = "abc1234def5678"
_COMMENT_ID = 101
_THREAD_ID = "PRRT_thread_node"

_INLINE_BODY = "\n\n".join(
    [
        "🔴 **P1** · `security` · `high confidence`",
        "**Fail-open default**",
        "Unknown status grants access.",
        "> [!IMPORTANT]",
        "> ⚡ **Prompt for AI agents**",
        ">",
        "> </details>",
    ],
)


class _FakeReporter:
    """Reporter double recording lifecycle edits and thread resolutions."""

    def __init__(
        self,
        *,
        threads: dict[int, ReviewThread] | None = None,
        edit_ok: bool = True,
        resolve_ok: bool = True,
    ) -> None:
        """Initialize the double.

        Args:
            threads: Thread map returned by ``fetch_review_threads``. ``None``
                simulates a failed listing.
            edit_ok: Whether comment edits succeed.
            resolve_ok: Whether the resolve mutation succeeds.
        """
        self.repo = "owner/name"
        self.pr_number = 7
        self.api_base = "https://api.github.com"
        self.edits: list[tuple[int, str]] = []
        self.resolved_threads: list[str] = []
        self._threads = threads
        self._edit_ok = edit_ok
        self._resolve_ok = resolve_ok

    def update_review_comment(self, *, comment_id: int, body: str) -> bool:
        """Record an inline comment edit.

        Args:
            comment_id: Comment being edited.
            body: New body.

        Returns:
            The configured outcome.
        """
        self.edits.append((comment_id, body))
        return self._edit_ok

    def fetch_review_threads(self) -> dict[int, ReviewThread] | None:
        """Return the configured thread map."""
        return self._threads

    def resolve_review_thread(self, *, thread_id: str) -> bool:
        """Record a thread resolution.

        Args:
            thread_id: Thread node id.

        Returns:
            The configured outcome.
        """
        self.resolved_threads.append(thread_id)
        return self._resolve_ok


def _record(**overrides: Any) -> FindingRecord:
    """Build a tracked finding record with lifecycle-relevant defaults.

    Args:
        **overrides: Field overrides.

    Returns:
        The record.
    """
    base = FindingRecord(
        fingerprint="a1b2c3d4e5f60718",
        ordinal=1,
        category="security",
        title="Fail-open default",
        file="src/main.py",
        line=10,
        since_round=1,
        inline_comment_id=_COMMENT_ID,
    )
    return replace(base, **overrides)


def _threads(*, is_resolved: bool = False) -> dict[int, ReviewThread]:
    """Build a thread map covering the fixture comment.

    Args:
        is_resolved: Whether the thread is already resolved.

    Returns:
        The map.
    """
    return {_COMMENT_ID: ReviewThread(node_id=_THREAD_ID, is_resolved=is_resolved)}


# --- rendering ---------------------------------------------------------------


def test_addressed_banner_names_the_fixing_commit_and_round() -> None:
    """The banner is the thread's outcome, so it must carry sha and round."""
    block = render_lifecycle_block(
        record=_record(status=FindingStatus.RESOLVED),
        stage=LifecycleStage.ADDRESSED,
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(block).contains("✔ **Addressed in `abc1234` · round 3**")


def test_applying_the_block_retitles_the_prompt_panel_as_historical() -> None:
    """A settled finding must not offer a live fix prompt."""
    block = render_lifecycle_block(
        record=_record(status=FindingStatus.RESOLVED),
        stage=LifecycleStage.ADDRESSED,
        head_sha=_HEAD_SHA,
        round_number=2,
    )

    body = apply_lifecycle_block(body=_INLINE_BODY, block=block, historical=True)

    assert_that(body).contains("> ⚡ **Prompt for AI agents (historical)**")
    assert_that(body).contains("✔ **Addressed in `abc1234` · round 2**")


def test_partial_progress_keeps_the_prompt_panel_live() -> None:
    """A pattern with occurrences left still wants an actionable prompt."""
    block = render_lifecycle_block(
        record=_record(
            occurrences=(FindingOccurrence(file="src/main.py", line=10),) * 6,
            occurrences_total=20,
        ),
        stage=LifecycleStage.PARTIAL,
        head_sha=_HEAD_SHA,
        round_number=4,
    )

    body = apply_lifecycle_block(body=_INLINE_BODY, block=block, historical=False)

    assert_that(body).contains("✔ **14/20 addressed in `abc1234` · round 4**")
    assert_that(body).does_not_contain("(historical)")


def test_reapplying_a_block_replaces_it_instead_of_stacking_banners() -> None:
    """Re-running a round must not leave two banners on one comment."""
    first = render_lifecycle_block(
        record=_record(status=FindingStatus.RESOLVED),
        stage=LifecycleStage.ADDRESSED,
        head_sha=_HEAD_SHA,
        round_number=2,
    )
    once = apply_lifecycle_block(body=_INLINE_BODY, block=first, historical=True)

    twice = apply_lifecycle_block(body=once, block=first, historical=True)

    assert_that(twice).is_equal_to(once)
    assert_that(twice.count(LIFECYCLE_OPEN)).is_equal_to(1)
    assert_that(twice.count("(historical)")).is_equal_to(1)


def test_regression_provenance_names_both_rounds_and_links_the_old_thread() -> None:
    """A re-raised finding must not read as brand new."""
    note = regression_provenance(
        record=_record(
            since_round=1,
            resolved_round=2,
            resolved_sha=_HEAD_SHA,
        ),
        thread_url="https://github.com/owner/name/pull/7#discussion_r101",
    )

    assert_that(note).contains("first raised round 1")
    assert_that(note).contains("fixed round 2")
    assert_that(note).contains("#discussion_r101")


def test_finding_marker_round_trips_and_rejects_junk_keys() -> None:
    """The marker is the only handle a later round has on a comment."""
    marker = finding_marker(key="a1b2c3d4e5f60718#2")

    assert_that(parse_finding_marker(body=f"body\n\n{marker}")).is_equal_to(
        "a1b2c3d4e5f60718#2",
    )
    assert_that(finding_marker(key="not a key -->")).is_empty()
    assert_that(parse_finding_marker(body="a human reply")).is_empty()


# --- synchronization ---------------------------------------------------------


def test_resolved_finding_is_bannered_and_its_thread_resolved_by_default() -> None:
    """``review.auto_resolve`` defaults to true, so the thread closes."""
    reporter = _FakeReporter(threads=_threads())
    record = _record(status=FindingStatus.RESOLVED, resolved_round=3)

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(report.edited).contains(record.key)
    assert_that(report.resolved).contains(record.key)
    assert_that(reporter.resolved_threads).is_equal_to([_THREAD_ID])
    assert_that(reporter.edits[0][1]).contains("✔ **Addressed in `abc1234` · round 3**")


def test_auto_resolve_false_keeps_the_banner_and_skips_the_mutation() -> None:
    """Opting out is a manual-ceremony choice, not a loss of information."""
    reporter = _FakeReporter(threads=_threads())
    record = _record(status=FindingStatus.RESOLVED, resolved_round=3)

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=3,
        auto_resolve=False,
    )

    assert_that(report.edited).contains(record.key)
    assert_that(report.resolved).is_empty()
    assert_that(reporter.resolved_threads).is_empty()
    assert_that(reporter.edits[0][1]).contains("Addressed in")


def test_partial_progress_never_resolves_the_thread() -> None:
    """A pattern is resolved only when every occurrence is gone (#1925)."""
    reporter = _FakeReporter(threads=_threads())
    record = _record(
        occurrences=(FindingOccurrence(file="src/main.py", line=10),) * 6,
        occurrences_total=20,
    )

    report = sync_addressed_lifecycle(
        reporter=reporter,
        partial=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=4,
    )

    assert_that(report.edited).contains(record.key)
    assert_that(report.resolved).is_empty()
    assert_that(reporter.resolved_threads).is_empty()
    assert_that(reporter.edits[0][1]).contains("14/20 addressed")


def test_regressed_thread_is_bannered_and_stays_resolved() -> None:
    """A resolved thread is never reopened; the regression gets a new one."""
    reporter = _FakeReporter(threads=_threads(is_resolved=True))
    record = _record(
        regressed=True,
        resolved_sha="0f0f0f0f",
        resolved_round=2,
    )

    report = sync_addressed_lifecycle(
        reporter=reporter,
        regressed=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=4,
        new_thread_urls={
            record.key: "https://github.com/owner/name/pull/7#discussion_r9",
        },
    )

    body = reporter.edits[0][1]
    assert_that(report.resolved).is_empty()
    assert_that(reporter.resolved_threads).is_empty()
    assert_that(body).contains("↩ **Regressed in `abc1234` · round 4**")
    assert_that(body).contains("This thread stays resolved.")
    assert_that(body).contains("#discussion_r9")


def test_a_regression_without_a_new_thread_does_not_link_one() -> None:
    """A finding off the diff gets no fresh comment, so promise none."""
    reporter = _FakeReporter(threads=_threads(is_resolved=True))
    record = _record(regressed=True, resolved_sha="0f0f0f0f", resolved_round=2)

    sync_addressed_lifecycle(
        reporter=reporter,
        regressed=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=4,
    )

    body = reporter.edits[0][1]
    assert_that(body).contains("↩ **Regressed in `abc1234` · round 4**")
    assert_that(body).contains("see the sticky comment's open findings")
    assert_that(body).does_not_contain("new thread]")


def test_one_thread_gets_one_banner_even_when_two_stages_apply() -> None:
    """A regression that also lost occurrences must not be edited twice.

    The second edit would start from the pre-edit body, undoing the first one's
    ``(historical)`` retitle and leaving a regression banner beside a live fix
    prompt. Regression wins: partial progress on a thread the discussion has
    already left is the less useful half of the story.
    """
    reporter = _FakeReporter(threads=_threads(is_resolved=True))
    record = _record(
        regressed=True,
        resolved_sha="0f0f0f0f",
        resolved_round=2,
        occurrences=(FindingOccurrence(file="src/main.py", line=10),) * 6,
        occurrences_total=20,
    )

    sync_addressed_lifecycle(
        reporter=reporter,
        partial=(record,),
        regressed=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=4,
    )

    assert_that(reporter.edits).is_length(1)
    body = reporter.edits[0][1]
    assert_that(body).contains("↩ **Regressed in")
    assert_that(body).contains("(historical)")
    assert_that(body).does_not_contain("14/20 addressed")


def test_a_regression_outranks_an_addressed_stamp_on_the_same_thread() -> None:
    """State D: the old thread must point at the new one, not read as fixed.

    The regression banner already carries the ``✔ Addressed in <sha>`` line for
    the round that had fixed the finding, so it loses nothing — while a bare
    "addressed" stamp would hide that the finding is open again elsewhere.
    """
    reporter = _FakeReporter(threads=_threads(is_resolved=True))
    record = _record(
        regressed=True,
        resolved_sha="0f0f0f0f",
        resolved_round=2,
    )
    url = "https://github.com/owner/name/pull/7#discussion_r9"

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        regressed=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=4,
        new_thread_urls={record.key: url},
    )

    assert_that(reporter.edits).is_length(1)
    body = reporter.edits[0][1]
    assert_that(body).contains("↩ **Regressed in `abc1234` · round 4**")
    assert_that(body).contains("#discussion_r9")
    # A regression never resolves the thread off its own back: it is already
    # resolved and the live discussion is on the fresh thread.
    assert_that(report.resolved).is_empty()
    assert_that(reporter.resolved_threads).is_empty()


def test_a_record_without_a_comment_id_is_never_stamped() -> None:
    """A finding whose thread was never captured has nothing to edit."""
    reporter = _FakeReporter(threads=_threads())
    record = _record(
        status=FindingStatus.RESOLVED,
        resolved_round=3,
        inline_comment_id=None,
    )

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(reporter.edits).is_empty()
    assert_that(report.edited).is_empty()
    assert_that(report.resolved).is_empty()
    assert_that(reporter.resolved_threads).is_empty()


def test_unchanged_body_is_never_patched() -> None:
    """The second run of a round must make no request at all."""
    reporter = _FakeReporter(threads=_threads(is_resolved=True))
    record = _record(status=FindingStatus.RESOLVED, resolved_round=3)
    stamped = apply_lifecycle_block(
        body=_INLINE_BODY,
        block=render_lifecycle_block(
            record=record,
            stage=LifecycleStage.ADDRESSED,
            head_sha=_HEAD_SHA,
            round_number=3,
        ),
        historical=True,
    )

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={_COMMENT_ID: stamped},
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(reporter.edits).is_empty()
    assert_that(report.unchanged).contains(record.key)


def test_a_failed_comment_edit_is_reported_rather_than_raised() -> None:
    """GitHub refusing one edit must not sink a review that found real bugs."""
    reporter = _FakeReporter(threads=_threads(), edit_ok=False)
    record = _record(status=FindingStatus.RESOLVED, resolved_round=3)

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(report.failed).contains(record.key)
    assert_that(report.edited).is_empty()
    # The thread is not resolved off the back of a banner that never landed.
    assert_that(reporter.resolved_threads).is_empty()
    assert_that(report.has_failures).is_true()


def test_a_failed_thread_listing_keeps_the_banner() -> None:
    """Resolution is best-effort; the recorded outcome is not."""
    reporter = _FakeReporter(threads=None)
    record = _record(status=FindingStatus.RESOLVED, resolved_round=3)

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={_COMMENT_ID: _INLINE_BODY},
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(report.edited).contains(record.key)
    assert_that(report.resolved).is_empty()
    assert_that(report.failed).contains(record.key)


def test_a_comment_whose_body_is_unknown_is_left_alone() -> None:
    """Overwriting an unread comment would destroy the finding it carries."""
    reporter = _FakeReporter(threads=_threads())
    record = _record(status=FindingStatus.RESOLVED, resolved_round=3)

    report = sync_addressed_lifecycle(
        reporter=reporter,
        resolved=(record,),
        comment_bodies={},
        head_sha=_HEAD_SHA,
        round_number=3,
    )

    assert_that(reporter.edits).is_empty()
    assert_that(report.edited).is_empty()
    assert_that(report.resolved).is_empty()


# --- end to end through the posting adapter ----------------------------------


def _sticky_body(*, state: ReviewState) -> str:
    """Render a prior sticky comment body carrying a state blob.

    Args:
        state: State to embed.

    Returns:
        The comment body.
    """
    return STICKY_MARKER + "\n\nprior round" + render_state_block(state=state)


def _prior_state(*, findings: tuple[FindingRecord, ...]) -> ReviewState:
    """Build a one-round prior state.

    Args:
        findings: Records tracked before this round.

    Returns:
        The state.
    """
    return ReviewState(runs=(RunRecord(round=1, sha="0f0f0f0"),), findings=findings)


def _posting_reporter(
    *,
    prior: ReviewState | None = None,
    review_comments: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Build a reporter stub whose PR diff covers both fixture findings.

    Args:
        prior: State to expose through the sticky comment, if any.
        review_comments: Inline comments the PR already carries.

    Returns:
        The stub reporter.
    """
    reporter = MagicMock()
    reporter.is_available.return_value = True
    if prior is None:
        # Round 1: the sticky does not exist until this run posts it, and is
        # then found on every later lookup — including the id capture's.
        lookups = iter([None])
        reporter.find_issue_comment.side_effect = lambda **_kwargs: next(
            lookups,
            (5, STICKY_MARKER),
        )
    else:
        reporter.find_issue_comment.return_value = (5, _sticky_body(state=prior))
    reporter.fetch_pr_diff_lines.return_value = {
        "src/main.py": {9, 10, 11},
        "tests/test_main.py": {5},
    }
    reporter.fetch_compare_lines.return_value = {"src/main.py": {9, 10, 11}}
    reporter.fetch_pr_commit_shas.return_value = []
    reporter.fetch_review_comments.return_value = review_comments or []
    reporter.fetch_review_threads.return_value = _threads()
    reporter.resolve_review_thread.return_value = True
    reporter.update_review_comment.return_value = True
    reporter.post_issue_comment.return_value = True
    reporter.update_issue_comment.return_value = True
    reporter.api_request.return_value = True
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def _posted_comments(*, reporter: MagicMock) -> list[dict[str, Any]]:
    """Return the inline comments of the submitted review payload.

    Args:
        reporter: Reporter stub the review was posted through.

    Returns:
        The ``comments`` array.
    """
    payload = reporter.api_request.call_args.args[2]
    comments: list[dict[str, Any]] = payload["comments"]
    return comments


def _persisted_state(*, reporter: MagicMock) -> dict[str, Any]:
    """Decode the state blob from the last sticky body written.

    Args:
        reporter: Reporter stub the review was posted through.

    Returns:
        The decoded state mapping.

    Raises:
        AssertionError: When no sticky body carrying a state blob was written.
    """
    bodies = [call.args[0] for call in reporter.post_issue_comment.call_args_list]
    bodies += [
        call.kwargs["body"] for call in reporter.update_issue_comment.call_args_list
    ]
    for body in reversed(bodies):
        if STATE_MARKER_PREFIX in body:
            encoded = body.rsplit(STATE_MARKER_PREFIX, 1)[1]
            decoded: dict[str, Any] = json.loads(
                encoded.rsplit(STATE_MARKER_SUFFIX, 1)[0].strip(),
            )
            return decoded
    msg = "no sticky body with a state blob was written"
    raise AssertionError(msg)


def _resolved_record(*, title: str, comment_id: int) -> FindingRecord:
    """Build a prior open record this round will not report again.

    Args:
        title: Finding title, chosen so it does not match the fixtures.
        comment_id: Inline comment anchoring the finding.

    Returns:
        The record.
    """
    return FindingRecord(
        fingerprint=fingerprint_for(
            file="src/old.py",
            category="performance",
            title=title,
        ),
        category="performance",
        title=title,
        file="src/old.py",
        line=4,
        inline_comment_id=comment_id,
    )


def test_inline_comments_carry_a_hidden_finding_marker(
    sample_review_result: ReviewResult,
) -> None:
    """Without the marker no later round can find the thread again."""
    reporter = _posting_reporter()

    post_review_to_github(result=sample_review_result, reporter=reporter)

    bodies = [comment["body"] for comment in _posted_comments(reporter=reporter)]
    assert_that(bodies).is_not_empty()
    for body in bodies:
        assert_that(parse_finding_marker(body=body)).is_not_empty()


def test_captured_comment_ids_are_persisted_for_the_next_round(
    sample_review_result: ReviewResult,
) -> None:
    """The banner edit needs an id, and the state blob is where it lives."""
    reporter = _posting_reporter()
    key = f"{fingerprint_for(file='src/main.py', category='security', title='Fail-open default')}#1"
    reporter.fetch_review_comments.return_value = [
        {"id": 555, "body": f"posted\n\n{finding_marker(key=key)}"},
    ]

    post_review_to_github(result=sample_review_result, reporter=reporter)

    state = _persisted_state(reporter=reporter)
    stored = {
        record["fingerprint"]: record.get("inline_comment_id")
        for record in state["findings"]
    }
    assert_that(stored).contains_entry({key.split("#")[0]: 555})


def test_a_finding_gone_from_this_round_is_bannered_and_resolved(
    sample_review_result: ReviewResult,
) -> None:
    """The default configuration closes the loop end to end."""
    record = _resolved_record(title="Slow loop", comment_id=_COMMENT_ID)
    reporter = _posting_reporter(
        prior=_prior_state(findings=(record,)),
        review_comments=[{"id": _COMMENT_ID, "body": _INLINE_BODY}],
    )

    post_review_to_github(result=sample_review_result, reporter=reporter)

    edit = reporter.update_review_comment.call_args
    assert_that(edit.kwargs["comment_id"]).is_equal_to(_COMMENT_ID)
    assert_that(edit.kwargs["body"]).contains("✔ **Addressed in")
    assert_that(edit.kwargs["body"]).contains("(historical)")
    reporter.resolve_review_thread.assert_called_once_with(thread_id=_THREAD_ID)


def test_auto_resolve_false_stops_the_mutation_end_to_end(
    sample_review_result: ReviewResult,
) -> None:
    """The opt-out reaches the adapter, not just the config model."""
    record = _resolved_record(title="Slow loop", comment_id=_COMMENT_ID)
    reporter = _posting_reporter(
        prior=_prior_state(findings=(record,)),
        review_comments=[{"id": _COMMENT_ID, "body": _INLINE_BODY}],
    )

    post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
        auto_resolve=False,
    )

    assert_that(reporter.update_review_comment.called).is_true()
    reporter.resolve_review_thread.assert_not_called()


def test_a_regression_posts_a_fresh_comment_carrying_its_provenance(
    sample_review_result: ReviewResult,
) -> None:
    """State D: new thread with history, old thread stamped and left resolved."""
    fingerprint = fingerprint_for(
        file="src/main.py",
        category="security",
        title="Fail-open default",
    )
    prior = FindingRecord(
        fingerprint=fingerprint,
        category="security",
        title="Fail-open default",
        file="src/main.py",
        line=10,
        status=FindingStatus.RESOLVED,
        since_round=1,
        resolved_sha="0f0f0f0",
        resolved_round=1,
        inline_comment_id=_COMMENT_ID,
    )
    reporter = _posting_reporter(
        prior=_prior_state(findings=(prior,)),
        review_comments=[{"id": _COMMENT_ID, "body": _INLINE_BODY}],
    )

    post_review_to_github(result=sample_review_result, reporter=reporter)

    fresh = [
        comment["body"]
        for comment in _posted_comments(reporter=reporter)
        if "↩ **regression**" in comment["body"]
    ]
    assert_that(fresh).is_length(1)
    assert_that(fresh[0]).contains("first raised round 1")
    edit = reporter.update_review_comment.call_args
    assert_that(edit.kwargs["body"]).contains("↩ **Regressed in")
    reporter.resolve_review_thread.assert_not_called()


@pytest.mark.parametrize("auto_resolve", [True, False])
def test_posting_survives_a_refused_comment_edit(
    sample_review_result: ReviewResult,
    auto_resolve: bool,
) -> None:
    """A refused banner degrades to "not stamped yet", never to a crash."""
    record = _resolved_record(title="Slow loop", comment_id=_COMMENT_ID)
    reporter = _posting_reporter(
        prior=_prior_state(findings=(record,)),
        review_comments=[{"id": _COMMENT_ID, "body": _INLINE_BODY}],
    )
    reporter.update_review_comment.return_value = False

    posted = post_review_to_github(
        result=sample_review_result,
        reporter=reporter,
        auto_resolve=auto_resolve,
    )

    assert_that(posted).is_true()
    # The record keeps its comment id, so the next round retries the banner.
    state = _persisted_state(reporter=reporter)
    stored = [
        item for item in state["findings"] if item["fingerprint"] == record.fingerprint
    ]
    assert_that(stored).is_length(1)
    assert_that(stored[0]["inline_comment_id"]).is_equal_to(_COMMENT_ID)


# --- transport ---------------------------------------------------------------


def _reader(payload: dict[str, Any]) -> MagicMock:
    """Build a urlopen context manager yielding ``payload`` as JSON.

    Args:
        payload: Response body.

    Returns:
        The context-manager mock.
    """
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    ctx = MagicMock()
    ctx.__enter__.return_value = response
    return ctx


def _api_reporter() -> GitHubPRReporter:
    """Build a real reporter pointed at github.com.

    Returns:
        The reporter.
    """
    return GitHubPRReporter(token=_TEST_TOKEN, repo="owner/name", pr_number=7)


def test_review_threads_join_on_the_threads_root_comment() -> None:
    """The stored comment id is the thread's first comment, and only that."""
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": _THREAD_ID,
                                "isResolved": True,
                                "comments": {"nodes": [{"databaseId": _COMMENT_ID}]},
                            },
                            {"id": None, "comments": {"nodes": []}},
                        ],
                    },
                },
            },
        },
    }

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        threads = _api_reporter().fetch_review_threads()

    assert_that(threads).is_equal_to(
        {_COMMENT_ID: ReviewThread(node_id=_THREAD_ID, is_resolved=True)},
    )


def test_graphql_errors_are_a_failure_even_under_a_200() -> None:
    """GraphQL answers 200 for a refused mutation; the errors array decides."""
    payload = {"data": {"resolveReviewThread": None}, "errors": [{"message": "nope"}]}

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        resolved = _api_reporter().resolve_review_thread(thread_id=_THREAD_ID)

    assert_that(resolved).is_false()


def test_resolve_review_thread_reports_the_threads_new_state() -> None:
    """Only GitHub confirming the resolution counts as resolved."""
    payload = {"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}

    with patch("urllib.request.urlopen", return_value=_reader(payload)):
        resolved = _api_reporter().resolve_review_thread(thread_id=_THREAD_ID)

    assert_that(resolved).is_true()


def test_update_review_comment_patches_the_pulls_comments_endpoint() -> None:
    """An inline comment lives under /pulls/comments, not /issues/comments."""
    reporter = _api_reporter()

    with patch.object(reporter, "api_request", return_value=True) as request:
        reporter.update_review_comment(comment_id=_COMMENT_ID, body="new")

    method, url, payload = request.call_args.args
    assert_that(method).is_equal_to("PATCH")
    assert_that(url).is_equal_to(
        f"https://api.github.com/repos/owner/name/pulls/comments/{_COMMENT_ID}",
    )
    assert_that(payload).is_equal_to({"body": "new"})


def test_review_comment_listing_degrades_to_none_on_a_transport_error() -> None:
    """A failed listing must not read as "this PR has no inline comments"."""
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        comments = _api_reporter().fetch_review_comments()

    assert_that(comments).is_none()


def test_graphql_url_follows_a_github_enterprise_api_base() -> None:
    """Enterprise serves GraphQL from /api/graphql, not /api/v3/graphql."""
    reporter = GitHubPRReporter(
        token=_TEST_TOKEN,
        repo="owner/name",
        pr_number=7,
        api_base="https://ghe.example.com/api/v3",
    )

    assert_that(reporter.graphql_url).is_equal_to("https://ghe.example.com/api/graphql")


def test_graphql_refuses_a_non_https_api_base() -> None:
    """A cleartext base must never carry the bearer token."""
    reporter = GitHubPRReporter(
        token=_TEST_TOKEN,
        repo="owner/name",
        pr_number=7,
        api_base="http://ghe.example.com/api/v3",
    )

    with patch("urllib.request.urlopen") as urlopen:
        resolved = reporter.resolve_review_thread(thread_id=_THREAD_ID)

    assert_that(resolved).is_false()
    urlopen.assert_not_called()
