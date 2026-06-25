"""Tests for review JSON output."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.output import (
    render_review_json,
    review_result_to_dict,
)


def test_review_result_to_dict_includes_metadata_fields(
    sample_review_result,
) -> None:
    """JSON dict includes all metadata fields."""
    payload = review_result_to_dict(result=sample_review_result)

    assert_that(payload["metadata"]).contains_key("model")
    assert_that(payload["metadata"]).contains_key("context_window")
    assert_that(payload["metadata"]).contains_key("timestamp")
    assert_that(payload["summary"]).is_equal_to("Merge with fixes.")
    assert_that(payload["checklist"]).is_length(2)
    assert_that(payload["findings"]).is_length(2)


def test_render_review_json_is_valid_json(sample_review_result) -> None:
    """Rendered JSON can be parsed back into a dictionary."""
    import json

    rendered = render_review_json(result=sample_review_result)
    payload = json.loads(rendered)

    assert_that(payload["findings"][0]["severity"]).is_equal_to("P1")
