"""A failed round keeps the mission-control sticky on screen (#1954)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AIAuthenticationError, AIProviderError
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.errors_taxonomy import KIND_COPY, ReviewErrorKind
from lintro.ai.review.github import post_review_error_to_github
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
)
from lintro.ai.review.github_errors import (
    BANNER_CAUSE_LIMIT,
    ERROR_ONLY_HEADLINE,
    FAILURE_BANNER_HEADLINE,
    condense_provider_error,
    format_error_comment,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import (
    advance_review_state,
    build_sticky_comment,
    render_state_sticky,
)

#: Banner headline for the round that fails in these tests, taken from the
#: production template so a copy change cannot silently defang the assertions.
_ROUND_2_FAILED = FAILURE_BANNER_HEADLINE.format(round_number=2)


@pytest.fixture
def prior_body(sample_review_result: ReviewResult) -> str:
    """Render a successful round-1 sticky to fail the next round against."""
    return build_sticky_comment(
        request=StickyRequest(
            result=sample_review_result,
            head_sha="a" * 40,
            transport="cli",
            auth_mode="subscription",
        ),
    )


@pytest.fixture
def prior_state(sample_review_result: ReviewResult) -> ReviewState:
    """Artifact state persisted by a successful round."""
    return advance_review_state(
        request=StickyRequest(
            result=sample_review_result,
            head_sha="a" * 40,
            transport="cli",
            auth_mode="subscription",
        ),
    )


def test_failure_after_success_renders_the_banner(prior_state: ReviewState) -> None:
    """The failed round is announced over the last good board, not instead."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        prior_state=prior_state,
    )

    assert_that(body).contains(f"> {_ROUND_2_FAILED}")
    assert_that(body).contains("showing round 1 results below")
    assert_that(body).contains(KIND_COPY[ReviewErrorKind.SERVER_ERROR][1])
    assert_that(body).contains("Overloaded")
    assert_that(body).does_not_contain(ERROR_ONLY_HEADLINE)


def test_banner_carries_the_kind_specific_guidance(prior_state: ReviewState) -> None:
    """A permanent failure must not be dressed up as a transient one.

    A rejected API key fails identically on every retry, so the banner has to
    close with the same advice the error-only surface would give rather than a
    blanket "retry shortly".
    """
    body = format_error_comment(
        error=AIAuthenticationError("401 unauthorized"),
        provider="anthropic",
        prior_state=prior_state,
    )
    assert_that(body).contains(f"> {_ROUND_2_FAILED}")
    assert_that(body).contains(KIND_COPY[ReviewErrorKind.AUTH_FAILED][1])
    assert_that(body).does_not_contain(KIND_COPY[ReviewErrorKind.SERVER_ERROR][1])


def test_banner_sits_directly_under_the_header(prior_state: ReviewState) -> None:
    r"""Nothing separates the failure notice from the sticky's title line.

    Asserted by line proximity rather than by index into the ``\\n\\n`` split:
    the guarantee is that a reader meets the banner immediately after the
    title, which must survive any section later gaining a blank line of its
    own.
    """
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )
    lines = body.splitlines()
    header_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("## 🔎 Lintro Review")
    )
    after_header = next(line for line in lines[header_index + 1 :] if line.strip())

    assert_that(lines[0]).is_equal_to(STICKY_MARKER)
    assert_that(after_header).starts_with(f"> {_ROUND_2_FAILED}")


def test_failure_after_success_renders_the_full_layout(
    prior_state: ReviewState,
) -> None:
    """Every state-derived mission-control section survives the failure."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )

    assert_that(body).contains("⛔ Blocked")
    assert_that(body).contains("### Findings ·")
    assert_that(body).contains("Fail-open default")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def test_first_round_failure_renders_the_error_only_surface() -> None:
    """With nothing to show, the failure is still the whole comment."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        prior_state=ReviewState(),
    )

    assert_that(body).contains(ERROR_ONLY_HEADLINE)
    assert_that(body).does_not_contain("showing round")
    assert_that(body).does_not_contain("### Findings ·")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def test_failed_round_leaves_the_state_blob_untouched(
    prior_body: str,
    prior_state: ReviewState,
) -> None:
    """A failed round does not write a leftover blob or advance the board."""
    del prior_body
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )

    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(prior_state.next_round).is_equal_to(2)


def test_failed_round_does_not_advance_the_round_counter(
    prior_state: ReviewState,
) -> None:
    """The next successful round still gets round 2, not round 3."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(prior_state.next_round).is_equal_to(2)
    assert_that(prior_state.runs).is_length(1)


def test_consecutive_failures_name_the_same_attempted_round(
    prior_state: ReviewState,
) -> None:
    """Repeated failures are repeated attempts at one round, not new rounds.

    A round number is assigned when a review completes, so a failed attempt has
    none to report and the banner names the round the next successful review
    will carry. Counting attempts instead would mean recording failures in the
    state blob, which a failed round must never do. The sticky is edited in
    place, so a reader only ever sees the latest attempt's banner.
    """
    first = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )
    second = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )

    assert_that(second).contains(f"> {_ROUND_2_FAILED}")
    assert_that(second).contains("showing round 1 results below")
    assert_that(first).does_not_contain(STATE_MARKER_PREFIX)
    assert_that(second).does_not_contain(STATE_MARKER_PREFIX)


def test_oversized_provider_error_is_truncated(prior_state: ReviewState) -> None:
    """A raw CLI payload is bounded and flattened onto the banner's one line."""
    payload = '{\n  "error": "' + ("x" * 20_000) + '"\n}'
    body = format_error_comment(
        error=AIProviderError(payload),
        prior_state=prior_state,
    )
    banner = next(
        line for line in body.splitlines() if line.startswith(f"> {_ROUND_2_FAILED}")
    )

    assert_that(banner).contains("…")
    assert_that(banner).contains("showing round 1 results below")
    assert_that(len(banner)).is_less_than(BANNER_CAUSE_LIMIT + 200)
    assert_that(body).does_not_contain("x" * (BANNER_CAUSE_LIMIT + 1))


def test_condense_provider_error_flattens_whitespace() -> None:
    """Newlines cannot break the detail out of its blockquote."""
    condensed = condense_provider_error(text="a\n\n  b\tc  ", limit=100)

    assert_that(condensed).is_equal_to("a b c")


def test_condense_provider_error_handles_empty_text() -> None:
    """A provider that reported nothing condenses to nothing."""
    assert_that(condense_provider_error(text="", limit=100)).is_empty()


def test_failure_body_respects_the_hard_comment_limit() -> None:
    """A huge board plus a failure banner still fits GitHub's cap."""
    findings = tuple(
        FindingRecord(
            fingerprint=f"fp{index:05d}",
            severity=Severity.P2,
            category="correctness",
            title=f"Finding {index} " + ("detail " * 20),
            file=f"src/module_{index}/handler.py",
            line=index,
            status=FindingStatus.OPEN,
            since_round=1,
        )
        for index in range(600)
    )
    state = ReviewState(
        runs=tuple(
            RunRecord(
                identity=RunIdentity(
                    round=round_number,
                    sha=f"{round_number:040d}",
                    model="m",
                ),
            )
            for round_number in range(1, 21)
        ),
        findings=findings,
    )

    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=state,
    )

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains(f"> {FAILURE_BANNER_HEADLINE.format(round_number=21)}")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def _reporter(*, prior_body: str, bodies: list[str] | None = None) -> MagicMock:
    """Build a mock reporter serving ``prior_body`` as the existing sticky.

    Args:
        prior_body: Sticky body the reporter reports as already posted.
        bodies: Optional list that collects every sticky body written back, so
            tests can assert on the rendered text rather than on mock call
            bookkeeping (#2315).

    Returns:
        The configured mock.
    """
    collected = bodies if bodies is not None else []

    def _update(**kwargs: Any) -> bool:
        """Record the body written back to the sticky comment.

        Args:
            **kwargs: Update keyword arguments, including ``body``.

        Returns:
            bool: Always ``True``, standing in for a successful edit.
        """
        collected.append(str(kwargs["body"]))
        return True

    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.side_effect = lambda *, marker: (
        (9, prior_body) if marker == STICKY_MARKER else None
    )
    reporter.lookup_issue_comments.return_value = {STICKY_MARKER: (9, prior_body)}
    reporter.update_issue_comment.side_effect = _update
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def test_posting_a_failure_updates_the_sticky_in_place(
    prior_body: str,
    prior_state: ReviewState,
) -> None:
    """The end-to-end error path edits the sticky and keeps the board."""
    bodies: list[str] = []
    reporter = _reporter(prior_body=prior_body, bodies=bodies)

    posted = post_review_error_to_github(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        repo="owner/name",
        pr_number=7,
        reporter=reporter,
        prior_state=prior_state,
    )
    body = bodies[-1]

    assert_that(posted).is_true()
    assert_that(body).contains(f"> {_ROUND_2_FAILED}")
    assert_that(body).contains("### Findings ·")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def test_posting_falls_back_to_the_reporter_pr_context(
    prior_body: str,
    prior_state: ReviewState,
) -> None:
    """Omitting the overrides renders exactly what supplying them renders."""
    explicit_bodies: list[str] = []
    implicit_bodies: list[str] = []
    explicit = _reporter(prior_body=prior_body, bodies=explicit_bodies)
    implicit = _reporter(prior_body=prior_body, bodies=implicit_bodies)

    post_review_error_to_github(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        repo="owner/name",
        pr_number=7,
        reporter=explicit,
        prior_state=prior_state,
    )
    post_review_error_to_github(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        reporter=implicit,
        prior_state=prior_state,
    )

    assert_that(explicit_bodies).is_length(1)
    assert_that(implicit_bodies).is_equal_to(explicit_bodies)
    assert_that(implicit_bodies[0]).contains("### Findings ·")


def test_render_state_sticky_without_a_banner(prior_state: ReviewState) -> None:
    """The renderer is usable on its own; the banner is opt-in."""
    body = render_state_sticky(state=prior_state)

    assert_that(body).contains("### Findings ·")
    assert_that(body).does_not_contain("could not complete")


def test_render_state_sticky_omits_this_round_only_sections(
    prior_state: ReviewState,
) -> None:
    """Sections describing a run that never happened are left out."""
    body = render_state_sticky(state=prior_state, banner="> ⚠️ nope")

    assert_that(body).does_not_contain("**This run**")
    assert_that(body).does_not_contain("### Summary")


@pytest.mark.parametrize("skip", [False, True])
def test_preparation_preserves_stored_board_without_embedded_blob(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    prior_state: ReviewState,
    skip: bool,
) -> None:
    """Preparation loads the actual stored ledger before updating a modern sticky."""
    from dataclasses import replace

    from lintro.ai.review import github_status
    from lintro.ai.review.exceptions import ReviewContextError
    from lintro.ai.review.state_store import write_local_state
    from lintro.cli_utils.commands import review as command

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("LINTRO_REVIEW_STATE_DIR", raising=False)
    stored = replace(prior_state, repo="owner/name", pr_number=7)
    path = write_local_state(state=stored, key="pr-7")
    before = path.read_bytes()
    bodies: list[str] = []
    sticky = render_state_sticky(state=stored)
    assert_that(sticky).does_not_contain(STATE_MARKER_PREFIX)
    reporter = _reporter(prior_body=sticky, bodies=bodies)
    monkeypatch.setattr(github_status, "GitHubPRReporter", lambda **_: reporter)
    options = MagicMock(post=True, output_format="json")
    targets = command._ReviewTargets(
        resolved_pr=7,
        state_pr=7,
        effective_repo="owner/name",
    )
    error = ReviewContextError(
        "gh pr diff timed out",
        code="no-changes" if skip else "gh-command-failed",
    )
    with pytest.raises(SystemExit) as caught:
        command._finish_context_failure(
            exc=error,
            options=options,
            targets=targets,
            provider_label="anthropic",
        )
    assert_that(caught.value.code).is_equal_to(0 if skip else 2)
    assert_that(bodies).is_length(1)
    assert_that(bodies[0]).contains(
        "### Findings ·",
        "No reviewable changes, no review ran." if skip else "gh pr diff timed out",
    )
    assert_that(path.read_bytes()).is_equal_to(before)


@pytest.mark.parametrize("load_fails", [False, True])
def test_no_changes_without_stored_state_leaves_modern_sticky_untouched(
    monkeypatch: pytest.MonkeyPatch,
    prior_state: ReviewState,
    load_fails: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unavailable ledger cannot erase a current sticky or turn a skip red."""
    from lintro.ai.review import github_status
    from lintro.ai.review.exceptions import ReviewContextError
    from lintro.cli_utils.commands import review as command

    bodies: list[str] = []
    reporter = _reporter(
        prior_body=render_state_sticky(state=prior_state),
        bodies=bodies,
    )
    monkeypatch.setattr(github_status, "GitHubPRReporter", lambda **_: reporter)

    def load(**_: object) -> ReviewState:
        """Return no recoverable state, or simulate an unavailable store."""
        if load_fails:
            raise OSError("state store unavailable")
        return ReviewState()

    monkeypatch.setattr(command, "load_prior_review_state", load)
    with pytest.raises(SystemExit) as caught:
        command._finish_context_failure(
            exc=ReviewContextError("No changes", code="no-changes"),
            options=MagicMock(post=True, output_format="terminal"),
            targets=command._ReviewTargets(
                resolved_pr=7,
                state_pr=7,
                effective_repo="owner/name",
            ),
            provider_label="anthropic",
        )
    assert_that(caught.value.code).is_equal_to(0)
    assert_that(capsys.readouterr().out).contains(
        "No reviewable changes, no review ran.",
    )
    assert_that(bodies).is_empty()
