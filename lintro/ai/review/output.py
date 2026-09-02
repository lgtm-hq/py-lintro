"""JSON serialization for AI review results."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.patch_validation import (
    count_dropped_suggestions,
    drop_reason_counts,
)

__all__ = [
    "render_review_json",
    "render_review_output",
    "review_result_to_dict",
    "review_result_to_json",
]


def review_result_to_dict(*, result: ReviewResult) -> dict[str, Any]:
    """Convert a review result to a JSON-serializable dictionary.

    The ``timings`` block carries the per-phase breakdown (#2148): ordered
    phase spans plus per-chunk queued/in-flight detail. It is ``None`` when
    the result predates timing instrumentation.

    The top-level ``coverage_complete`` / ``coverage_degradations`` /
    ``findings_cap_applied`` / ``output_exhaustion_retried`` keys report
    whether a CLI findings cap or an output-exhaustion retry may have
    suppressed findings (#2003). They are always present, so a classifier can
    tell "the model found N issues" from "we capped the model at N".

    Args:
        result: Review result to serialize.

    Returns:
        Dictionary representation suitable for JSON encoding.
    """
    metadata = asdict(result.metadata)
    # Timings are hoisted out of ``metadata`` into a top-level ``timings``
    # block (#2148): the breakdown is run instrumentation, not review
    # content, and consumers should not have to dig for it.
    metadata.pop("timings", None)
    # #2003: ``asdict`` renders the degradations as raw dataclass dicts with a
    # StrEnum reason; normalize them through the model's own serializer so the
    # reason is a plain string on every consumer.
    degradations = [item.to_dict() for item in result.metadata.coverage_degradations]
    metadata["coverage_degradations"] = degradations
    timings = result.metadata.timings
    payload: dict[str, Any] = {
        "metadata": metadata,
        "timings": timings.to_dict() if timings is not None else None,
        "summary": result.summary,
        "readiness_verdict": result.readiness_verdict.value,
        "pr_summary": (
            asdict(result.pr_summary) if result.pr_summary is not None else None
        ),
        "verdict_reasoning": (
            asdict(result.verdict_reasoning)
            if result.verdict_reasoning is not None
            else None
        ),
        "file_assessments": [
            asdict(assessment) for assessment in result.file_assessments
        ],
        "checklist": [asdict(answer) for answer in result.checklist],
        "findings": [asdict(finding) for finding in result.findings],
        # #2101: dropped suggestions are never silent. Each finding carries its
        # own ``suggestion_dropped`` tag; these keys give consumers the run
        # total without re-deriving it from the finding list.
        "suggestions_dropped": count_dropped_suggestions(findings=result.findings),
        "suggestions_dropped_by_reason": drop_reason_counts(
            findings=result.findings,
        ),
        # #2003: a capped or retried run is never presented as a complete one.
        # ``partial`` stays the "chunks went unreviewed" axis; these keys are
        # the sibling "reviewed, but not at full depth" axis.
        "coverage_complete": result.metadata.coverage_complete,
        "coverage_degradations": degradations,
        "findings_cap_applied": result.metadata.findings_cap_applied,
        "output_exhaustion_retried": result.metadata.output_exhaustion_retried,
    }
    if result.coverage is not None:
        payload["coverage"] = result.coverage.to_dict()
        payload["partial"] = result.metadata.partial
        payload["stopped_reason"] = result.metadata.stopped_reason
    return payload


def review_result_to_json(*, result: ReviewResult) -> str:
    """Serialize a review result to pretty-printed JSON.

    Args:
        result: Review result to serialize.

    Returns:
        JSON string with two-space indentation.
    """
    return json.dumps(review_result_to_dict(result=result), indent=2)


def render_review_json(*, result: ReviewResult) -> str:
    """Render review result as JSON text.

    Args:
        result: Review result to render.

    Returns:
        Pretty-printed JSON string.
    """
    return review_result_to_json(result=result)


def render_review_output(
    *,
    result: ReviewResult,
    output_format: str = "terminal",
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> str | None:
    """Dispatch review output rendering by format.

    Args:
        result: Review result to render.
        output_format: ``terminal`` or ``json``.
        checklist_display: Structured checklist visibility for terminal output.
        question_map: Prompt id to question text for linked terminal display.

    Returns:
        JSON string when ``output_format`` is ``json``; otherwise ``None``.
    """
    if output_format.lower() == "json":
        return render_review_json(result=result)

    from lintro.ai.review.display import render_review_terminal

    render_review_terminal(
        result=result,
        checklist_display=checklist_display,
        question_map=question_map,
    )
    return None
