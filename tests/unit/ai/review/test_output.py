"""Tests for review JSON output."""

from __future__ import annotations

import json
from dataclasses import replace

from assertpy import assert_that

from lintro.ai.review.models.merged_duplicate import MergedDuplicate
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
    assert_that(payload["findings"]).is_length(2)
    assert_that(payload).does_not_contain_key("checklist")


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


def test_review_result_json_carries_custom_agent_attribution(
    sample_review_result: ReviewResult,
) -> None:
    """Findings serialize a ``source`` field for custom agent attribution."""
    attributed = replace(
        sample_review_result,
        findings=(replace(sample_review_result.findings[0], source="no-raw-sql"),),
    )

    payload = review_result_to_dict(result=attributed)

    assert_that(payload["findings"][0]["source"]).is_equal_to("no-raw-sql")
    assert_that(payload["metadata"]).contains_key("custom_agents_run")
    assert_that(payload["metadata"]).contains_key("custom_agents_skipped")


def test_merged_duplicates_is_absent_when_no_merge_folded_a_finding_in(
    sample_review_result: ReviewResult,
) -> None:
    """A finding no duplicate merge touched carries no ``merged_duplicates`` key."""
    payload = review_result_to_dict(result=sample_review_result)

    for finding in payload["findings"]:
        assert_that(finding).does_not_contain_key("merged_duplicates")


def test_merged_duplicates_is_serialized_when_a_merge_folded_a_finding_in(
    sample_review_result: ReviewResult,
) -> None:
    """A survivor names each finding the merge folded into it."""
    merged = MergedDuplicate(
        file="src/other.py",
        category="security",
        title="Unknown status grants access",
        line=42,
    )
    survivor = replace(
        sample_review_result,
        findings=(
            replace(
                sample_review_result.findings[0],
                merged_duplicates=(merged,),
            ),
        ),
    )

    payload = review_result_to_dict(result=survivor)

    assert_that(payload["findings"][0]["merged_duplicates"]).is_equal_to(
        (
            {
                "file": "src/other.py",
                "category": "security",
                "title": "Unknown status grants access",
                "line": 42,
            },
        ),
    )
    rendered = json.loads(render_review_json(result=survivor))

    assert_that(rendered["findings"][0]).contains_key("merged_duplicates")
