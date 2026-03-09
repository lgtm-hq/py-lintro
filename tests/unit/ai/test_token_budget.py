"""Tests for token budget estimation and truncation utilities."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.token_budget import estimate_tokens, truncate_to_budget

# -- estimate_tokens ---------------------------------------------------------


def test_empty_string_returns_zero() -> None:
    """An empty string produces 0 tokens."""
    assert_that(estimate_tokens("")).is_equal_to(0)


def test_short_string_returns_at_least_one() -> None:
    """A non-empty string shorter than 4 chars still returns 1."""
    assert_that(estimate_tokens("hi")).is_equal_to(1)


def test_four_chars_equals_one_token() -> None:
    """4 characters maps to exactly 1 token."""
    assert_that(estimate_tokens("abcd")).is_equal_to(1)


def test_eight_chars_equals_two_tokens() -> None:
    """8 characters maps to 2 tokens."""
    assert_that(estimate_tokens("abcdefgh")).is_equal_to(2)


# -- truncate_to_budget ------------------------------------------------------


def test_no_truncation_when_under_budget() -> None:
    """Text under the budget is returned as-is with truncated=False."""
    text = "hello"
    result, truncated = truncate_to_budget(text, max_tokens=100)
    assert_that(result).is_equal_to(text)
    assert_that(truncated).is_false()


def test_truncation_sets_flag() -> None:
    """Text over budget returns truncated=True."""
    text = "a" * 100
    result, truncated = truncate_to_budget(text, max_tokens=5)
    assert_that(truncated).is_true()
    assert_that(len(result)).is_less_than(len(text))


def test_truncation_cuts_at_newline() -> None:
    """Truncation prefers cutting at a newline boundary."""
    text = "line1\nline2\nline3\nline4\nline5\n"
    result, truncated = truncate_to_budget(text, max_tokens=3)
    # max_tokens=3 -> max_chars=12 -> cuts at newline boundary
    assert_that(truncated).is_true()
    assert_that(result).contains("\n")
    # Result should not contain later lines
    assert_that(result).does_not_contain("line3")
    assert_that(result).does_not_contain("line4")


def test_empty_string_no_truncation() -> None:
    """An empty string is never truncated."""
    result, truncated = truncate_to_budget("", max_tokens=10)
    assert_that(result).is_equal_to("")
    assert_that(truncated).is_false()
