"""Tests for CLI-transport large-diff limits (#1967)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.review.cli_limits import (
    CLI_DIFF_HARD_CEILING_BYTES,
    REVIEW_CHUNK_DIFF_TOKEN_BUDGET,
    assert_cli_diff_within_ceiling,
    is_cli_output_exhaustion,
    is_output_exhaustion_error,
    measure_diff_size,
    resolve_chunk_diff_budget,
)
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.review_context import ReviewContext


def _context(*, unified_diff: str) -> ReviewContext:
    """Build a minimal review context with the given diff."""
    return ReviewContext(
        base_ref="main",
        head_ref="HEAD",
        changed_files=[],
        unified_diff=unified_diff,
        pr_metadata=None,
        repo_root="/tmp",
    )


def test_measure_diff_size_counts_lines_bytes_and_tokens() -> None:
    """Diff measurement reports lines, UTF-8 bytes, and estimated tokens."""
    diff = "a\nb\nc"
    size = measure_diff_size(unified_diff=diff)
    assert_that(size.lines).is_equal_to(3)
    assert_that(size.bytes).is_equal_to(len(diff.encode("utf-8")))
    assert_that(size.tokens).is_greater_than(0)


def test_resolve_chunk_diff_budget_takes_minimum() -> None:
    """CLI soft ceiling wins when the context-window budget is larger."""
    budget = resolve_chunk_diff_budget(
        context_window_budget=200_000,
        review_chunk_diff_tokens=REVIEW_CHUNK_DIFF_TOKEN_BUDGET,
    )
    assert_that(budget).is_equal_to(REVIEW_CHUNK_DIFF_TOKEN_BUDGET)


def test_assert_cli_diff_within_ceiling_accepts_small_diffs() -> None:
    """Diffs under the hard ceiling do not raise."""
    assert_cli_diff_within_ceiling(
        context=_context(unified_diff="diff --git a/x b/x\n+ok\n"),
        cli_max_diff_bytes=CLI_DIFF_HARD_CEILING_BYTES,
    )


def test_assert_cli_diff_within_ceiling_rejects_oversized_diffs() -> None:
    """Oversized diffs raise a diff-too-large context error with advice."""
    huge = "x" * (CLI_DIFF_HARD_CEILING_BYTES + 1)
    with pytest.raises(ReviewContextError) as exc_info:
        assert_cli_diff_within_ceiling(
            context=_context(unified_diff=huge),
            cli_max_diff_bytes=CLI_DIFF_HARD_CEILING_BYTES,
        )
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_TOO_LARGE)
    assert_that(str(exc_info.value)).contains("--paths")
    assert_that(str(exc_info.value)).contains("--transport api")


def test_cli_limits_expose_no_findings_cap() -> None:
    """The per-call findings cap is gone from the CLI limits for good.

    lintro-ops milestone 0 (decision A) retired the cap: a chunk reports every
    finding it has, and an oversized answer splits the chunk instead. Pinning
    the absence keeps a future "just a small ceiling" from creeping back.
    """
    import lintro.ai.review.cli_limits as cli_limits

    for name in (
        "CLI_MAX_FINDINGS_PER_CALL",
        "CLI_FINDINGS_RETRY_CAP",
        "resolve_cli_findings_cap",
        "tighter_findings_cap",
        "findings_cap_was_hit",
    ):
        assert_that(hasattr(cli_limits, name)).described_as(name).is_false()


def test_is_output_exhaustion_error_matches_known_signatures() -> None:
    """Known 32k / length-limit messages are classified as exhaustion."""
    assert_that(
        is_output_exhaustion_error('... "stop_reason": "max_tokens" ...'),
    ).is_true()
    assert_that(is_output_exhaustion_error("connection reset")).is_false()
    # Every CLI failure envelope carries is_error/output_tokens/finish_reason;
    # generic envelope noise must NOT classify as exhaustion (auth 403 shown).
    assert_that(
        is_output_exhaustion_error(
            'Claude CLI exited with code 1: {"is_error":true,'
            '"usage":{"input_tokens":0,"output_tokens":0},'
            '"api_error_status":403,"result":"subscription disabled"}',
        ),
    ).is_false()
    assert_that(
        is_cli_output_exhaustion(
            AIProviderError("maximum output tokens exceeded"),
        ),
    ).is_true()
    # Input context-window violations and generic proxy truncation share
    # wording with output exhaustion; a tighter findings cap cannot fix
    # either, so they must not trigger the retry.
    assert_that(
        is_output_exhaustion_error(
            "prompt exceeded the maximum number of tokens for this model",
        ),
    ).is_false()
    assert_that(
        is_output_exhaustion_error("upstream proxy error: response truncated"),
    ).is_false()


#: The exhaustion prose a Claude CLI envelope quotes verbatim (#1967 fixtures).
_EXHAUSTED_PROSE = "Claude CLI reported error: maximum output tokens reached"


def test_rejected_requests_quoting_exhaustion_prose_are_not_exhaustion() -> None:
    """A 400 or 429 that mentions output tokens is a refusal, not an overrun.

    Splitting the input cannot repair an invalid ``max_tokens`` parameter or
    a rate limit, so neither may trigger the split-and-retry path (#2695).
    """
    # Codex's case: an invalid-request response whose prose names the cap.
    assert_that(
        is_output_exhaustion_error(
            "400 Bad Request: requested max_tokens exceeds maximum output "
            "tokens for this model",
        ),
    ).is_false()
    # The Claude CLI's API-error envelope, as the provider now surfaces it.
    assert_that(
        is_output_exhaustion_error(
            "Claude CLI reported error: maximum output tokens for your plan "
            '({"terminal_reason":"api_error","api_error_status":429})',
        ),
    ).is_false()
    # Vendor error types carry the same verdict without an HTTP status.
    assert_that(
        is_output_exhaustion_error(
            '{"type":"invalid_request_error","message":"max output tokens"}',
        ),
    ).is_false()
    # A 4xx number only counts in status position; token counts and model
    # names that happen to contain one must not mask a genuine overrun.
    assert_that(
        is_output_exhaustion_error(
            "maximum output tokens reached after 400 tokens of JSON",
        ),
    ).is_true()
    assert_that(
        is_output_exhaustion_error("claude-4-0429: hit max output tokens"),
    ).is_true()
    assert_that(
        is_output_exhaustion_error("HTTP 429: maximum output tokens for plan"),
    ).is_false()
    assert_that(
        is_output_exhaustion_error("status_code=400 max output tokens invalid"),
    ).is_false()
    # The genuine CLI overrun and the structured fields still classify.
    assert_that(is_output_exhaustion_error(_EXHAUSTED_PROSE)).is_true()
    assert_that(
        is_output_exhaustion_error('... "stop_reason": "max_tokens" ...'),
    ).is_true()


def test_typed_provider_errors_are_never_output_exhaustion() -> None:
    """Rate-limit and auth errors subclass AIProviderError but never split."""
    from lintro.ai.retry import _is_output_exhausted

    exhausted_budget = AIRateLimitError(
        "Rate limit retries exhausted. Last provider error: " + _EXHAUSTED_PROSE,
    )
    auth = AIAuthenticationError(_EXHAUSTED_PROSE)
    for error in (exhausted_budget, auth):
        assert_that(is_cli_output_exhaustion(error)).described_as(
            type(error).__name__,
        ).is_false()
        assert_that(_is_output_exhausted(error=error)).described_as(
            type(error).__name__,
        ).is_false()
    plain = AIProviderError(_EXHAUSTED_PROSE)
    assert_that(is_cli_output_exhaustion(plain)).is_true()
    assert_that(_is_output_exhausted(error=plain)).is_true()


def test_measure_diff_size_empty_and_multibyte() -> None:
    """Empty diffs measure zero; byte counts follow UTF-8, not len()."""
    empty = measure_diff_size(unified_diff="")
    assert_that(empty.lines).is_equal_to(0)
    assert_that(empty.bytes).is_equal_to(0)
    assert_that(empty.tokens).is_equal_to(0)

    emoji = measure_diff_size(unified_diff="+🎉\n")
    assert_that(emoji.lines).is_equal_to(1)
    assert_that(emoji.bytes).is_equal_to(len("+🎉\n".encode()))
