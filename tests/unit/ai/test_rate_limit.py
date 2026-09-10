"""Tests for ``Retry-After`` extraction on provider 429s (#2506)."""

from __future__ import annotations

from email.utils import formatdate
from types import SimpleNamespace

import pytest
from assertpy import assert_that

from lintro.ai.rate_limit import (
    MAX_RETRY_AFTER_SECONDS,
    parse_retry_after,
    retry_after_from_exception,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", 3.0),
        ("  12  ", 12.0),
        ("0", 0.0),
        (None, None),
        ("", None),
        ("soon", None),
        ("-5", None),
        ("nan", None),
        ("inf", None),
        ("-inf", None),
    ],
)
def test_parse_retry_after_reads_delta_seconds(
    raw: str | None,
    expected: float | None,
) -> None:
    """Delta-seconds values parse; junk and negatives degrade to ``None``.

    Args:
        raw: Header value under test.
        expected: Expected parsed delay.
    """
    assert_that(parse_retry_after(raw)).is_equal_to(expected)


def test_non_finite_values_never_reach_the_sleep_call() -> None:
    """``float()`` accepts nan/inf; RFC 9110 delta-seconds never are.

    ``nan`` in particular evades a plain ``< 0`` test and survives
    ``min``, so it would reach ``asyncio.sleep`` and raise instead of
    falling back to the exponential backoff.
    """
    for raw in ("nan", "NaN", "inf", "Infinity", "-inf"):
        assert_that(parse_retry_after(raw)).described_as(raw).is_none()


def test_parse_retry_after_reads_an_http_date() -> None:
    """The HTTP-date form becomes a delay relative to the given clock."""
    now = 1_800_000_000.0
    header = formatdate(timeval=now + 45.0, usegmt=True)

    assert_that(parse_retry_after(header, now_timestamp=now)).is_equal_to(45.0)


def test_parse_retry_after_drops_a_past_http_date() -> None:
    """An already-elapsed HTTP-date is not a wait instruction."""
    now = 1_800_000_000.0
    header = formatdate(timeval=now - 60.0, usegmt=True)

    assert_that(parse_retry_after(header, now_timestamp=now)).is_none()


def test_parse_retry_after_caps_an_absurd_wait() -> None:
    """A multi-hour wait is capped so a job budget cannot be blown."""
    assert_that(parse_retry_after("86400")).is_equal_to(MAX_RETRY_AFTER_SECONDS)


def test_retry_after_from_exception_reads_the_sdk_response() -> None:
    """Both SDKs hang the offending response off the error."""
    error = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "7"}))

    assert_that(retry_after_from_exception(error)).is_equal_to(7.0)


def test_retry_after_from_exception_falls_back_to_headers() -> None:
    """Some error paths expose ``headers`` without a response object."""
    error = SimpleNamespace(headers={"retry-after": "2"})

    assert_that(retry_after_from_exception(error)).is_equal_to(2.0)


def test_retry_after_from_exception_is_none_without_headers() -> None:
    """An SDK error with no headers must not raise inside the handler."""
    assert_that(retry_after_from_exception(RuntimeError("429"))).is_none()


def test_retry_after_from_exception_ignores_a_missing_header() -> None:
    """A 429 response without the header leaves the caller on its backoff."""
    error = SimpleNamespace(response=SimpleNamespace(headers={}))

    assert_that(retry_after_from_exception(error)).is_none()
