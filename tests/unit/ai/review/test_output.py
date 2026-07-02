"""Tests for review JSON output."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.output import (
    render_review_json,
    render_review_output,
    review_result_to_dict,
)


def test_review_result_to_dict_includes_metadata_fields(
    sample_review_result: ReviewResult,
) -> None:
    """JSON dict includes all metadata fields."""
    payload = review_result_to_dict(result=sample_review_result)

    assert_that(payload["metadata"]).contains_key("model")
    assert_that(payload["metadata"]).contains_key("context_window")
    assert_that(payload["metadata"]).contains_key("timestamp")
    assert_that(payload["summary"]).is_equal_to("Merge with fixes.")
    assert_that(payload["checklist"]).is_length(2)
    assert_that(payload["findings"]).is_length(2)


def test_render_review_json_is_valid_json(
    sample_review_result: ReviewResult,
) -> None:
    """Rendered JSON can be parsed back into a dictionary."""
    rendered = render_review_json(result=sample_review_result)
    payload = json.loads(rendered)

    assert_that(payload["summary"]).is_equal_to("Merge with fixes.")
    assert_that(payload["findings"]).is_length(2)
    severities = {finding["severity"] for finding in payload["findings"]}
    assert_that(severities).is_equal_to({"P1", "P2"})


def test_render_review_output_json_dispatches_to_render_review_json(
    sample_review_result: ReviewResult,
) -> None:
    """JSON output format routes through render_review_json."""
    output = render_review_output(result=sample_review_result, output_format="json")
    expected = render_review_json(result=sample_review_result)

    assert_that(output).is_equal_to(expected)
