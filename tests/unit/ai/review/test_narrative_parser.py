"""Tests for narrative review output parsing and degradation (#1907)."""

from __future__ import annotations

from typing import Any, TypeVar

import pytest
from assertpy import assert_that

from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.narrative_parser import (
    MAX_WALKTHROUGH_BULLETS,
    collapse_to_single_line,
    parse_file_assessments,
    parse_narrative,
    parse_review_summary,
    parse_summary_text,
    parse_verdict_reasoning,
)

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


def _rich_payload() -> dict[str, Any]:
    """Build a review payload carrying every narrative field.

    Returns:
        A payload in the extended review response shape.
    """
    return {
        "summary": {
            "headline": "Adds structured narrative outputs to the review call.",
            "walkthrough": [
                {"text": "Extends the response schema.", "finding_ref": ""},
                {"text": "Derives the verdict in code.", "finding_ref": "a.py:12"},
            ],
        },
        "verdict_reasoning": {
            "deciding_factor": "A null dereference in the merge path.",
            "failure_mechanism": "Any chunked review crashes before rendering.",
            "files_needing_attention": ["a.py", "b.py"],
        },
        "file_assessments": [
            {"file": "a.py", "overview": "Adds the merge helper."},
            {"file": "b.py", "overview": "Threads the new fields through."},
        ],
        "checklist": [],
        "findings": [],
    }


def test_parse_narrative_reads_every_field() -> None:
    """A fully populated payload parses into all three narrative records."""
    summary, reasoning, assessments = parse_narrative(payload=_rich_payload())

    parsed_summary = _require(summary)
    parsed_reasoning = _require(reasoning)
    assert_that(parsed_summary.headline).starts_with("Adds structured narrative")
    assert_that(parsed_summary.walkthrough).is_length(2)
    assert_that(parsed_summary.walkthrough[1].finding_ref).is_equal_to("a.py:12")
    assert_that(parsed_reasoning.failure_mechanism).contains("chunked review")
    assert_that(parsed_reasoning.files_needing_attention).is_equal_to(
        ("a.py", "b.py"),
    )
    assert_that([item.file for item in assessments]).is_equal_to(["a.py", "b.py"])


def test_parse_narrative_degrades_on_legacy_string_summary() -> None:
    """A pre-#1907 payload yields no narrative records and never raises."""
    payload = {"summary": "Merge with fixes.", "checklist": [], "findings": []}

    summary, reasoning, assessments = parse_narrative(payload=payload)

    assert_that(summary).is_none()
    assert_that(reasoning).is_none()
    assert_that(assessments).is_empty()


def test_parse_summary_text_reads_both_summary_shapes() -> None:
    """The flat summary text comes from a string or an object headline."""
    assert_that(parse_summary_text(raw_summary="Plain text. ")).is_equal_to(
        "Plain text.",
    )
    assert_that(
        parse_summary_text(raw_summary={"headline": "Structured text."}),
    ).is_equal_to("Structured text.")


@pytest.mark.parametrize(
    "raw_summary",
    [None, "", [], {"walkthrough": []}, {"headline": "  "}],
    ids=[
        "summary=missing",
        "summary=empty-string",
        "summary=wrong-type",
        "summary=no-headline",
        "summary=blank-headline",
    ],
)
def test_parse_review_summary_degrades_to_none(raw_summary: object) -> None:
    """Absent or contentless summaries degrade to ``None``."""
    assert_that(parse_review_summary(raw_summary=raw_summary)).is_none()


def test_parse_review_summary_accepts_plain_string_bullets() -> None:
    """Bullets written as bare strings parse with an empty finding reference."""
    summary = parse_review_summary(
        raw_summary={"headline": "Does a thing.", "walkthrough": ["First.", "Second."]},
    )

    walkthrough = _require(summary).walkthrough
    assert_that(walkthrough).is_length(2)
    assert_that(walkthrough[0].text).is_equal_to("First.")
    assert_that(walkthrough[0].finding_ref).is_empty()


def test_parse_review_summary_caps_walkthrough_bullets() -> None:
    """An over-long walkthrough is truncated rather than rejected."""
    summary = parse_review_summary(
        raw_summary={
            "headline": "Does a thing.",
            "walkthrough": [f"Bullet {index}." for index in range(20)],
        },
    )

    assert_that(_require(summary).walkthrough).is_length(MAX_WALKTHROUGH_BULLETS)


@pytest.mark.parametrize(
    "raw_reasoning",
    [None, "prose", {"deciding_factor": "   "}, {}],
    ids=[
        "reasoning=missing",
        "reasoning=wrong-type",
        "reasoning=blank",
        "reasoning=empty-object",
    ],
)
def test_parse_verdict_reasoning_degrades_to_none(raw_reasoning: object) -> None:
    """Absent or contentless reasoning degrades to ``None``."""
    assert_that(parse_verdict_reasoning(raw_reasoning=raw_reasoning)).is_none()


def test_parse_verdict_reasoning_keeps_partial_content() -> None:
    """Reasoning with only a deciding factor is still returned."""
    reasoning = parse_verdict_reasoning(
        raw_reasoning={"deciding_factor": "Nothing blocks the merge."},
    )

    parsed = _require(reasoning)
    assert_that(parsed.deciding_factor).is_equal_to("Nothing blocks the merge.")
    assert_that(parsed.failure_mechanism).is_empty()
    assert_that(parsed.files_needing_attention).is_empty()


def test_parse_file_assessments_drops_unusable_entries() -> None:
    """Entries without a path, and repeats of a path, are dropped."""
    assessments = parse_file_assessments(
        raw_assessments=[
            {"file": "a.py", "overview": "First."},
            {"overview": "No path."},
            "not an object",
            {"file": "a.py", "overview": "Duplicate."},
        ],
    )

    assert_that(assessments).is_length(1)
    assert_that(assessments[0].overview).is_equal_to("First.")


def test_parse_file_assessments_drops_entries_with_no_overview() -> None:
    """A valid file path with a missing or blank overview is dropped too.

    A blank FileAssessment.overview would render an empty per-file bullet,
    violating the contract that every kept assessment has real content.
    """
    assessments = parse_file_assessments(
        raw_assessments=[
            {"file": "a.py", "overview": ""},
            {"file": "b.py"},
            {"file": "c.py", "overview": "Has content."},
        ],
    )

    assert_that(assessments).is_length(1)
    assert_that(assessments[0].file).is_equal_to("c.py")


@pytest.mark.parametrize(
    "raw_assessments",
    [None, {}, "prose"],
    ids=["assessments=missing", "assessments=object", "assessments=prose"],
)
def test_parse_file_assessments_degrades_to_empty(raw_assessments: object) -> None:
    """A non-list assessments value degrades to an empty tuple."""
    assert_that(parse_file_assessments(raw_assessments=raw_assessments)).is_empty()


def test_collapse_to_single_line_normalizes_whitespace() -> None:
    """Newlines and runs of whitespace collapse to single spaces."""
    assert_that(
        collapse_to_single_line(text="  Title\nspanning\t\tlines  "),
    ).is_equal_to("Title spanning lines")


def test_parse_findings_enforces_single_line_titles() -> None:
    """A multi-line model title is flattened before it reaches a renderer."""
    findings = parse_findings(
        raw_findings=[
            {
                "severity": "P1",
                "category": "logic-bug",
                "file": "a.py",
                "line": 3,
                "title": "Broken title\nsecond line",
                "description": "d",
                "cause": "c",
                "fix": "f",
                "confidence": "high",
            },
        ],
    )

    assert_that(findings[0].title).is_equal_to("Broken title second line")
