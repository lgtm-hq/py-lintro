"""A failed round keeps the mission-control sticky on screen (#1954)."""

from __future__ import annotations

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
from lintro.ai.review.github_sticky import (
    build_sticky_comment,
    parse_review_state_v2,
    render_state_sticky,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord

#: Banner headline for the round that fails in these tests, taken from the
#: production template so a copy change cannot silently defang the assertions.
_ROUND_2_FAILED = FAILURE_BANNER_HEADLINE.format(round_number=2)


def _state_block(*, body: str) -> str:
    """Return the hidden state block of a sticky body.

    Args:
        body: Rendered sticky comment body.

    Returns:
        Everything from the state marker onwards.
    """
    index = body.find(STATE_MARKER_PREFIX)
    assert_that(index).described_as("state block offset").is_not_equal_to(-1)
    return body[index:]


@pytest.fixture
def prior_body(sample_review_result: ReviewResult) -> str:
    """Render a successful round-1 sticky to fail the next round against."""
    return build_sticky_comment(
        result=sample_review_result,
        head_sha="a" * 40,
        transport="cli",
        auth_mode="subscription",
    )


@pytest.fixture
def prior_state(prior_body: str) -> ReviewState:
    """Decode the state persisted by a successful round."""
    return parse_review_state_v2(body=prior_body)


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


def test_legacy_prior_runs_also_render_the_board(prior_state: ReviewState) -> None:
    """A v1 sticky's run mappings route to the board, not the error surface."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_runs=[run.to_dict() for run in prior_state.runs],
    )

    assert_that(body).contains(f"> {_ROUND_2_FAILED}")
    assert_that(body).contains("showing round 1 results below")
    assert_that(body).does_not_contain(ERROR_ONLY_HEADLINE)
    assert_that(parse_review_state_v2(body=body).runs).is_length(
        len(prior_state.runs),
    )


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
        if line.startswith("## 🔎 Lintro Review · round 1")
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
    assert_that(body).contains("| 🔴 blockers | 🟠 warnings | 🟡 nits | ✔ fixed |")
    assert_that(body).contains("### Open findings")
    assert_that(body).contains("Fail-open default")
    assert_that(body).contains(STATE_MARKER_PREFIX)


def test_first_round_failure_renders_the_error_only_surface() -> None:
    """With nothing to show, the failure is still the whole comment."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        prior_state=ReviewState(),
    )

    assert_that(body).contains(ERROR_ONLY_HEADLINE)
    assert_that(body).does_not_contain("showing round")
    assert_that(body).does_not_contain("### Open findings")
    assert_that(body).does_not_contain(STATE_MARKER_PREFIX)


def test_failed_round_leaves_the_state_blob_untouched(
    prior_body: str,
    prior_state: ReviewState,
) -> None:
    """A failed round persists the prior state byte-for-byte."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )

    assert_that(_state_block(body=body)).is_equal_to(_state_block(body=prior_body))


def test_failed_round_does_not_advance_the_round_counter(
    prior_state: ReviewState,
) -> None:
    """The next successful round still gets round 2, not round 3."""
    body = format_error_comment(
        error=AIProviderError("Overloaded"),
        prior_state=prior_state,
    )
    recovered = parse_review_state_v2(body=body)

    assert_that(recovered.next_round).is_equal_to(2)
    assert_that(recovered.runs).is_length(len(prior_state.runs))
    assert_that(recovered.findings).is_equal_to(prior_state.findings)


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
        prior_state=parse_review_state_v2(body=first),
    )

    assert_that(second).contains(f"> {_ROUND_2_FAILED}")
    assert_that(second).contains("showing round 1 results below")
    assert_that(_state_block(body=second)).is_equal_to(_state_block(body=first))


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
            RunRecord(round=round_number, sha=f"{round_number:040d}", model="m")
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
    assert_that(body).contains(STATE_MARKER_PREFIX)


def _reporter(*, prior_body: str) -> MagicMock:
    """Build a mock reporter serving ``prior_body`` as the existing sticky.

    Args:
        prior_body: Sticky body the reporter reports as already posted.

    Returns:
        The configured mock.
    """
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = (9, prior_body)
    reporter.update_issue_comment.return_value = True
    reporter.repo = "owner/name"
    reporter.pr_number = 7
    return reporter


def test_posting_a_failure_updates_the_sticky_in_place(prior_body: str) -> None:
    """The end-to-end error path edits the sticky and keeps the board."""
    reporter = _reporter(prior_body=prior_body)

    posted = post_review_error_to_github(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        repo="owner/name",
        pr_number=7,
        reporter=reporter,
    )
    body = reporter.update_issue_comment.call_args.kwargs["body"]

    assert_that(posted).is_true()
    assert_that(body).contains(f"> {_ROUND_2_FAILED}")
    assert_that(body).contains("### Open findings")
    assert_that(_state_block(body=body)).is_equal_to(_state_block(body=prior_body))


def test_posting_falls_back_to_the_reporter_pr_context(prior_body: str) -> None:
    """Omitting the overrides renders exactly what supplying them renders."""
    explicit = _reporter(prior_body=prior_body)
    implicit = _reporter(prior_body=prior_body)

    post_review_error_to_github(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        repo="owner/name",
        pr_number=7,
        reporter=explicit,
    )
    post_review_error_to_github(
        error=AIProviderError("Overloaded"),
        provider="anthropic",
        reporter=implicit,
    )

    assert_that(implicit.update_issue_comment.call_args.kwargs["body"]).is_equal_to(
        explicit.update_issue_comment.call_args.kwargs["body"],
    )


def test_render_state_sticky_without_a_banner(prior_state: ReviewState) -> None:
    """The renderer is usable on its own; the banner is opt-in."""
    body = render_state_sticky(state=prior_state)

    assert_that(body).contains("### Open findings")
    assert_that(body).does_not_contain("could not complete")


def test_render_state_sticky_omits_this_round_only_sections(
    prior_state: ReviewState,
) -> None:
    """Sections describing a run that never happened are left out."""
    body = render_state_sticky(state=prior_state, banner="> ⚠️ nope")

    assert_that(body).does_not_contain("**This run**")
    assert_that(body).does_not_contain("### Summary")
