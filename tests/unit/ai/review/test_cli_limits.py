"""Tests for CLI-transport large-diff limits (#1967)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AIProviderError
from lintro.ai.review.cli_limits import (
    CLI_DIFF_HARD_CEILING_BYTES,
    CLI_FINDINGS_RETRY_CAP,
    CLI_MAX_FINDINGS_PER_CALL,
    CLI_TRANSPORT_DIFF_TOKEN_BUDGET,
    assert_cli_diff_within_ceiling,
    is_cli_output_exhaustion,
    is_output_exhaustion_error,
    measure_diff_size,
    resolve_cli_diff_budget,
    resolve_cli_findings_cap,
    tighter_findings_cap,
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


def test_resolve_cli_diff_budget_takes_minimum() -> None:
    """CLI soft ceiling wins when the context-window budget is larger."""
    budget = resolve_cli_diff_budget(
        context_window_budget=200_000,
        cli_max_diff_tokens=CLI_TRANSPORT_DIFF_TOKEN_BUDGET,
    )
    assert_that(budget).is_equal_to(CLI_TRANSPORT_DIFF_TOKEN_BUDGET)


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


def test_resolve_cli_findings_cap_only_for_cli() -> None:
    """Findings cap applies only on the CLI transport."""
    assert_that(
        resolve_cli_findings_cap(
            transport_is_cli=True,
            cli_max_findings_per_call=CLI_MAX_FINDINGS_PER_CALL,
        ),
    ).is_equal_to(CLI_MAX_FINDINGS_PER_CALL)
    assert_that(
        resolve_cli_findings_cap(
            transport_is_cli=False,
            cli_max_findings_per_call=CLI_MAX_FINDINGS_PER_CALL,
        ),
    ).is_none()


def test_tighter_findings_cap_steps_down() -> None:
    """Retry caps step down toward one finding."""
    assert_that(tighter_findings_cap(current=12)).is_equal_to(CLI_FINDINGS_RETRY_CAP)
    assert_that(tighter_findings_cap(current=CLI_FINDINGS_RETRY_CAP)).is_equal_to(3)
    assert_that(tighter_findings_cap(current=1)).is_equal_to(1)


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


def test_measure_diff_size_empty_and_multibyte() -> None:
    """Empty diffs measure zero; byte counts follow UTF-8, not len()."""
    empty = measure_diff_size(unified_diff="")
    assert_that(empty.lines).is_equal_to(0)
    assert_that(empty.bytes).is_equal_to(0)
    assert_that(empty.tokens).is_equal_to(0)

    emoji = measure_diff_size(unified_diff="+🎉\n")
    assert_that(emoji.lines).is_equal_to(1)
    assert_that(emoji.bytes).is_equal_to(len("+🎉\n".encode()))
