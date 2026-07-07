"""JSON serialization for AI review results."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
    "render_review_json",
    "render_review_output",
    "review_result_to_dict",
    "review_result_to_json",
]


def review_result_to_dict(*, result: ReviewResult) -> dict[str, Any]:
    """Convert a review result to a JSON-serializable dictionary.

    Args:
        result: Review result to serialize.

    Returns:
        Dictionary representation suitable for JSON encoding.
    """
    metadata = asdict(result.metadata)
    return {
        "metadata": metadata,
        "summary": result.summary,
        "checklist": [asdict(answer) for answer in result.checklist],
        "findings": [asdict(finding) for finding in result.findings],
    }


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
