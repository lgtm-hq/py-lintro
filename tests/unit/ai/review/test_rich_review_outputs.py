"""Tests for the findings-only chunk contract and the narrative surfaces (#1907, lintro-ops #37)."""

from __future__ import annotations

import json
from typing import Any, TypeVar, cast

import pytest
from assertpy import assert_that

from lintro.ai.cli_schemas import REVIEW_CLI_SCHEMA
from lintro.ai.prompts.review import REVIEW_OUTPUT_SCHEMA, format_output_rules
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.merge import ChunkReviewPartial, merge_review_results
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.summary_bullet import SummaryBullet
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.response_pipeline import payload_to_partial
from lintro.ai.review.verdict import VERDICT_LABELS, VERDICT_RUBRIC_FINE_PRINT

_T = TypeVar("_T")


def _require(value: _T | None) -> _T:
    """Return a value that the test requires to be present.

    Args:
        value: Optional value produced by the code under test.

    Returns:
        The value itself.
    """
    if value is None:
        pytest.fail("expected a value, got None")
    return value


def _response() -> AIResponse:
    """Build a provider response stub for payload parsing.

    Returns:
        A response carrying only the usage fields the parser reads.
    """
    return AIResponse(
        content="{}",
        model="test-model",
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.01,
    )


def _partial(*, findings: tuple[ReviewFinding, ...] = ()) -> ChunkReviewPartial:
    """Build a findings-only chunk partial.

    Args:
        findings: Findings the chunk reported.

    Returns:
        The constructed partial.
    """
    return ChunkReviewPartial(
        findings=findings,
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
    )


def _payload() -> dict[str, Any]:
    """Build an extended review payload for one chunk.

    Returns:
        A payload in the extended review response shape.
    """
    return {
        "summary": {
            "headline": "Adds narrative outputs.",
            "walkthrough": [{"text": "Extends the schema.", "finding_ref": "a.py:1"}],
        },
        "verdict_reasoning": {
            "deciding_factor": "Nothing blocks the merge.",
            "failure_mechanism": "",
            "files_needing_attention": [],
        },
        "file_assessments": [{"file": "a.py", "overview": "Adds the schema."}],
        "checklist": [],
        "findings": [],
    }


def test_payload_to_partial_ignores_narrative_keys() -> None:
    """A chunk answer is findings only; narrative keys are ignored, not parsed."""
    partial = payload_to_partial(response=_response(), payload=_payload())

    assert_that(partial.findings).is_empty()
    assert_that(partial.flagged_files).is_empty()
    for name in ("summary", "pr_summary", "file_assessments", "checklist"):
        assert_that(hasattr(partial, name)).described_as(name).is_false()


def test_payload_to_partial_reads_a_findings_only_payload() -> None:
    """The findings-only contract parses findings and re-read flags."""
    partial = payload_to_partial(
        response=_response(),
        payload={
            "findings": [
                {
                    "severity": "P2",
                    "category": "logic-bug",
                    "file": "a.py",
                    "line": 3,
                    "title": "Off by one",
                    "description": "d",
                    "cause": "c",
                    "fix": "f",
                    "confidence": "high",
                },
            ],
            "flagged_files": [{"path": "b.py", "reason": "re-read"}],
        },
    )

    assert_that(partial.findings).is_length(1)
    assert_that(partial.findings[0].title).is_equal_to("Off by one")
    assert_that(partial.flagged_files).is_length(1)


def test_merge_review_results_carries_findings_only() -> None:
    """The merged shell has no narrative of its own; synthesis writes it."""
    merged = merge_review_results(partials=[_partial(), _partial()])

    assert_that(merged.summary).is_equal_to("")
    assert_that(merged.pr_summary).is_none()
    assert_that(merged.verdict_reasoning).is_none()
    assert_that(hasattr(merged, "file_assessments")).is_false()
    assert_that(hasattr(merged, "checklist")).is_false()


def test_review_result_to_dict_includes_narrative_and_verdict() -> None:
    """Serialized results expose the narrative fields and derived verdict."""
    result = ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
        ),
        summary="Adds a parser.",
        findings=(
            ReviewFinding(
                severity=Severity.P1,
                category="logic-bug",
                file="a.py",
                line=1,
                title="Boom",
                description="d",
                cause="c",
                fix="f",
                confidence="high",
            ),
        ),
        pr_summary=ReviewSummary(
            headline="Adds a parser.",
            walkthrough=(SummaryBullet(text="Parses.", finding_ref="a.py:1"),),
        ),
        verdict_reasoning=VerdictReasoning(deciding_factor="A crash on merge."),
    )

    payload = review_result_to_dict(result=result)

    assert_that(payload["readiness_verdict"]).is_equal_to(ReviewVerdict.BLOCKED.value)
    assert_that(payload["pr_summary"]["walkthrough"][0]["finding_ref"]).is_equal_to(
        "a.py:1",
    )
    assert_that(payload["verdict_reasoning"]["deciding_factor"]).is_equal_to(
        "A crash on merge.",
    )
    assert_that(payload).does_not_contain_key("file_assessments")
    assert_that(payload).does_not_contain_key("checklist")


def test_review_result_to_dict_degrades_without_narrative() -> None:
    """A TL;DR-only result serializes with null narrative fields."""
    result = ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
        ),
        summary="Merge with fixes.",
    )

    payload = review_result_to_dict(result=result)

    assert_that(payload["pr_summary"]).is_none()
    assert_that(payload["verdict_reasoning"]).is_none()
    assert_that(payload).does_not_contain_key("file_assessments")
    assert_that(payload["readiness_verdict"]).is_equal_to(ReviewVerdict.READY.value)


def test_prompt_output_schema_is_findings_only() -> None:
    """The chunk prompt schema declares findings and re-read flags, nothing else."""
    schema = json.loads(REVIEW_OUTPUT_SCHEMA)

    assert_that(set(schema)).is_equal_to({"findings", "flagged_files"})
    assert_that(schema["findings"][0]["title"]).contains("single-line")


def test_cli_schema_matches_prompt_schema_fields() -> None:
    """The strict CLI schema accepts exactly the fields the prompt requests."""
    properties = cast(dict[str, Any], REVIEW_CLI_SCHEMA["properties"])
    prompt_schema = json.loads(REVIEW_OUTPUT_SCHEMA)

    assert_that(set(properties)).is_equal_to(set(prompt_schema))
    assert_that(REVIEW_CLI_SCHEMA["required"]).is_equal_to(["findings"])
    assert_that(set(properties["findings"]["items"]["properties"])).is_equal_to(
        set(prompt_schema["findings"][0]),
    )


def test_output_rules_forbid_a_model_supplied_verdict() -> None:
    """The prompt tells the model the verdict is derived, not scored."""
    rules = format_output_rules(checklist_count=3)

    assert_that(rules).contains("Do not score or state a verdict")
    assert_that(rules).contains("findings only")
    assert_that(rules).contains("a later pass writes the summary")
    assert_that(rules).contains("single line with no line breaks")
    assert_that(rules).does_not_contain("checklist entries")


def test_prompt_rubric_names_the_same_verdicts_as_the_code_rubric() -> None:
    """The rubric shown to the model and the rendered one cannot drift apart."""
    rules = format_output_rules(checklist_count=1)

    for verdict, label in VERDICT_LABELS.items():
        if verdict is ReviewVerdict.INCOMPLETE:
            continue
        assert_that(rules).contains(label)
        assert_that(VERDICT_RUBRIC_FINE_PRINT).contains(label)
