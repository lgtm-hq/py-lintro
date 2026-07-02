"""JSON serialization for AI review results."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
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


def render_review_output(
    *,
    result: ReviewResult,
    output_format: str = "terminal",
) -> str | None:
    """Dispatch review output rendering by format.

    Args:
        result: Review result to render.
        output_format: ``terminal`` or ``json``.

    Returns:
        JSON string when ``output_format`` is ``json``; otherwise ``None``.
    """
    if output_format.lower() == "json":
        return review_result_to_json(result=result)

    from lintro.ai.review.display import render_review_terminal

    render_review_terminal(result=result)
    return None
