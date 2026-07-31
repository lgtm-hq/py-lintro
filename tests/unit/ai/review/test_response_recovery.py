"""Tests for prose-response recovery helpers (#1853)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.prompts.review import (
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SCHEMA_REMINDER_TEMPLATE,
)
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.response_recovery import (
    MAX_ECHOED_RESPONSE_CHARS,
    SCHEMA_RETRY_MIN_TIMEOUT,
    UNSTRUCTURED_CATEGORY,
    build_schema_reminder_prompt,
    resolve_schema_retry_timeout,
    unstructured_review_payload,
)

_PROSE = "Two actionable findings:\n\n1. The observer re-arms after close.\n2. ..."


def test_retry_gets_the_remaining_budget_capped_at_half() -> None:
    """A fast first call still cannot buy a second full-length call."""
    assert_that(
        resolve_schema_retry_timeout(api_timeout=900.0, elapsed=10.0),
    ).is_equal_to(450.0)


def test_retry_shrinks_as_the_first_call_consumes_the_budget() -> None:
    """What remains of the per-call budget bounds the retry."""
    assert_that(
        resolve_schema_retry_timeout(api_timeout=900.0, elapsed=600.0),
    ).is_equal_to(300.0)


def test_retry_is_skipped_when_the_budget_is_spent() -> None:
    """A first call that ate the budget goes straight to the prose fallback."""
    assert_that(
        resolve_schema_retry_timeout(api_timeout=900.0, elapsed=890.0),
    ).is_none()


def test_retry_is_skipped_below_the_minimum_useful_timeout() -> None:
    """Half of a short budget is not enough time to be worth spending."""
    assert_that(
        resolve_schema_retry_timeout(
            api_timeout=SCHEMA_RETRY_MIN_TIMEOUT * 2 - 1,
            elapsed=0.0,
        ),
    ).is_none()


@pytest.mark.parametrize("api_timeout", [0.0, -5.0])
def test_retry_is_skipped_for_a_nonpositive_budget(api_timeout: float) -> None:
    """A missing or nonsensical timeout never authorises an extra call."""
    assert_that(
        resolve_schema_retry_timeout(api_timeout=api_timeout, elapsed=0.0),
    ).is_none()


def test_reminder_prompt_carries_schema_and_previous_answer() -> None:
    """The reminder asks for a conversion, not a fresh review."""
    prompt = build_schema_reminder_prompt(
        template=REVIEW_SCHEMA_REMINDER_TEMPLATE,
        output_schema=REVIEW_OUTPUT_SCHEMA,
        previous_response=_PROSE,
    )

    assert_that(prompt).contains(_PROSE)
    assert_that(prompt).contains("Do not repeat the review")


def test_reminder_prompt_caps_the_echoed_answer() -> None:
    """A huge prose answer cannot blow up the reminder prompt."""
    huge = "y" * (MAX_ECHOED_RESPONSE_CHARS + 5000)

    prompt = build_schema_reminder_prompt(
        template=REVIEW_SCHEMA_REMINDER_TEMPLATE,
        output_schema=REVIEW_OUTPUT_SCHEMA,
        previous_response=huge,
    )

    assert_that(len(prompt)).is_less_than(len(huge))
    assert_that(prompt).contains("truncated in this reminder only")


def test_unstructured_payload_preserves_the_full_prose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prose survives verbatim as both summary and finding description."""
    monkeypatch.chdir(tmp_path)
    long_prose = _PROSE + ("z" * 4000)

    payload = unstructured_review_payload(
        content=long_prose,
        files=("src/main.py",),
    )

    assert_that(payload["summary"]).contains(long_prose)
    assert_that(payload["findings"]).is_length(1)
    finding = payload["findings"][0]
    assert_that(finding["description"]).is_equal_to(long_prose)
    assert_that(finding["category"]).is_equal_to(UNSTRUCTURED_CATEGORY)
    assert_that(finding["severity"]).is_equal_to(Severity.P3.value)
    assert_that(finding["file"]).is_equal_to("src/main.py")


def test_unstructured_payload_matches_the_review_payload_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback payload carries the keys the review parser requires."""
    monkeypatch.chdir(tmp_path)

    payload = unstructured_review_payload(content=_PROSE)

    assert_that(payload).contains_key("summary", "checklist", "findings")
    assert_that(payload["checklist"]).is_empty()
    assert_that(payload["findings"][0]["file"]).is_equal_to("")
