"""Tests for narrative review outputs end to end through the pipeline (#1907)."""

from __future__ import annotations

import json
from typing import Any, TypeVar, cast

import pytest
from assertpy import assert_that

from lintro.ai.cli_schemas import REVIEW_CLI_SCHEMA
from lintro.ai.prompts.review import REVIEW_OUTPUT_SCHEMA, format_output_rules
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.merge import (
    ChunkReviewPartial,
    merge_pr_summaries,
    merge_review_results,
)
from lintro.ai.review.models.file_assessment import FileAssessment
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


def _partial(
    *,
    summary: str = "",
    pr_summary: ReviewSummary | None = None,
    verdict_reasoning: VerdictReasoning | None = None,
    file_assessments: tuple[FileAssessment, ...] = (),
) -> ChunkReviewPartial:
    """Build a chunk partial carrying only the narrative fields under test.

    Args:
        summary: Flat summary text.
        pr_summary: Structured summary, if any.
        verdict_reasoning: Verdict reasoning, if any.
        file_assessments: Per-file assessments.

    Returns:
        The constructed partial.
    """
    return ChunkReviewPartial(
        summary=summary,
        checklist=(),
        findings=(),
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
        pr_summary=pr_summary,
        verdict_reasoning=verdict_reasoning,
        file_assessments=file_assessments,
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


def test_payload_to_partial_carries_narrative_fields() -> None:
    """A chunk partial keeps the structured narrative alongside the flat text."""
    partial = payload_to_partial(response=_response(), payload=_payload())

    assert_that(partial.summary).is_equal_to("Adds narrative outputs.")
    assert_that(_require(partial.pr_summary).walkthrough[0].text).is_equal_to(
        "Extends the schema.",
    )
    assert_that(_require(partial.verdict_reasoning).deciding_factor).is_equal_to(
        "Nothing blocks the merge.",
    )
    assert_that(partial.file_assessments[0].file).is_equal_to("a.py")


def test_payload_to_partial_degrades_on_legacy_payload() -> None:
    """A findings-only legacy payload still produces a usable partial."""
    partial = payload_to_partial(
        response=_response(),
        payload={"summary": "Merge with fixes.", "checklist": [], "findings": []},
    )

    assert_that(partial.summary).is_equal_to("Merge with fixes.")
    assert_that(partial.pr_summary).is_none()
    assert_that(partial.verdict_reasoning).is_none()
    assert_that(partial.file_assessments).is_empty()


def test_merge_pr_summaries_drops_an_all_blank_headline_result() -> None:
    """A merge with no usable headline text returns None, not a blank one.

    parse_review_summary treats a summary with bullets but no headline as
    non-None (is_empty requires both fields absent), so every chunk can
    contribute a headline-less summary. Joining empty headlines would then
    leave a structurally invalid ReviewSummary(headline="", ...) that
    renderers would print as a blank heading line.
    """
    merged = merge_pr_summaries(
        partials=[
            _partial(
                pr_summary=ReviewSummary(
                    headline="",
                    walkthrough=(SummaryBullet(text="Parses the payload."),),
                ),
            ),
            _partial(
                pr_summary=ReviewSummary(
                    headline="",
                    walkthrough=(SummaryBullet(text="Threads it through."),),
                ),
            ),
        ],
    )

    assert_that(merged).is_none()


def test_merge_review_results_merges_narrative_across_chunks() -> None:
    """Headlines join, bullets deduplicate, and file assessments key by path."""
    merged = merge_review_results(
        partials=[
            _partial(
                summary="First chunk.",
                pr_summary=ReviewSummary(
                    headline="Adds a parser.",
                    walkthrough=(SummaryBullet(text="Parses the payload."),),
                ),
                verdict_reasoning=VerdictReasoning(
                    deciding_factor="Nothing blocks the merge.",
                    files_needing_attention=("a.py",),
                ),
                file_assessments=(FileAssessment(file="a.py", overview="Parser."),),
            ),
            _partial(
                summary="Second chunk.",
                pr_summary=ReviewSummary(
                    headline="Wires it in.",
                    walkthrough=(
                        SummaryBullet(text="Parses the payload."),
                        SummaryBullet(text="Threads it through."),
                    ),
                ),
                verdict_reasoning=VerdictReasoning(
                    deciding_factor="Ignored — the first chunk's prose wins.",
                    files_needing_attention=("b.py",),
                ),
                file_assessments=(FileAssessment(file="b.py", overview="Wiring."),),
            ),
        ],
    )

    assert_that(_require(merged.pr_summary).headline).is_equal_to(
        "Adds a parser. Wires it in.",
    )
    assert_that(
        [bullet.text for bullet in _require(merged.pr_summary).walkthrough],
    ).is_equal_to(
        ["Parses the payload.", "Threads it through."],
    )
    assert_that(_require(merged.verdict_reasoning).deciding_factor).is_equal_to(
        "Nothing blocks the merge.",
    )
    assert_that(_require(merged.verdict_reasoning).files_needing_attention).is_equal_to(
        ("a.py", "b.py"),
    )
    assert_that([item.file for item in merged.file_assessments]).is_equal_to(
        ["a.py", "b.py"],
    )


def test_merge_review_results_without_narrative_yields_none() -> None:
    """Chunks that produced no narrative merge to the degraded shape."""
    merged = merge_review_results(partials=[_partial(summary="Only text.")])

    assert_that(merged.pr_summary).is_none()
    assert_that(merged.verdict_reasoning).is_none()
    assert_that(merged.file_assessments).is_empty()


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
        file_assessments=(FileAssessment(file="a.py", overview="Parser."),),
    )

    payload = review_result_to_dict(result=result)

    assert_that(payload["readiness_verdict"]).is_equal_to(ReviewVerdict.BLOCKED.value)
    assert_that(payload["pr_summary"]["walkthrough"][0]["finding_ref"]).is_equal_to(
        "a.py:1",
    )
    assert_that(payload["verdict_reasoning"]["deciding_factor"]).is_equal_to(
        "A crash on merge.",
    )
    assert_that(payload["file_assessments"]).is_length(1)


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
    assert_that(payload["file_assessments"]).is_empty()
    assert_that(payload["readiness_verdict"]).is_equal_to(ReviewVerdict.READY.value)


def test_prompt_output_schema_declares_narrative_fields() -> None:
    """The prompt schema is valid JSON declaring every narrative field."""
    schema = json.loads(REVIEW_OUTPUT_SCHEMA)

    assert_that(schema["summary"]).contains_key("headline", "walkthrough")
    assert_that(schema["verdict_reasoning"]).contains_key(
        "deciding_factor",
        "failure_mechanism",
        "files_needing_attention",
    )
    assert_that(schema["file_assessments"][0]).contains_key("file", "overview")
    assert_that(schema["findings"][0]["title"]).contains("single-line")


def test_cli_schema_matches_prompt_schema_fields() -> None:
    """The strict CLI schema accepts exactly the fields the prompt requests."""
    properties = cast(dict[str, Any], REVIEW_CLI_SCHEMA["properties"])
    prompt_schema = json.loads(REVIEW_OUTPUT_SCHEMA)

    assert_that(set(properties)).is_equal_to(set(prompt_schema))
    assert_that(set(properties["summary"]["properties"])).is_equal_to(
        set(prompt_schema["summary"]),
    )
    assert_that(set(properties["findings"]["items"]["properties"])).is_equal_to(
        set(prompt_schema["findings"][0]),
    )
    assert_that(set(properties["verdict_reasoning"]["properties"])).is_equal_to(
        set(prompt_schema["verdict_reasoning"]),
    )
    assert_that(
        set(properties["file_assessments"]["items"]["properties"]),
    ).is_equal_to(set(prompt_schema["file_assessments"][0]))


def test_output_rules_forbid_a_model_supplied_verdict() -> None:
    """The prompt tells the model the verdict is derived, not scored."""
    rules = format_output_rules(checklist_count=3)

    assert_that(rules).contains("Do not score or state a verdict")
    assert_that(rules).contains("computed by")
    assert_that(rules).contains("single line with no line breaks")
    assert_that(rules).contains("**3**")


def test_prompt_rubric_names_the_same_verdicts_as_the_code_rubric() -> None:
    """The rubric shown to the model and the rendered one cannot drift apart."""
    rules = format_output_rules(checklist_count=1)

    for verdict, label in VERDICT_LABELS.items():
        if verdict is ReviewVerdict.INCOMPLETE:
            continue
        assert_that(rules).contains(label)
        assert_that(VERDICT_RUBRIC_FINE_PRINT).contains(label)
