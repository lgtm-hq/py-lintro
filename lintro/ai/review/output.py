"""JSON serialization for AI review results."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.patch_validation import (
    count_dropped_suggestions,
    drop_reason_counts,
)
from lintro.ai.review.severity_gate import count_cross_chunk_contradictions

#: Key the CI classifier looks for in the captured review log to tell a
#: sticky-only round from one whose findings reached inline comments (#2266).
INLINE_POST_FAILURE_KEY = "inline_post_failure"

__all__ = [
    "INLINE_POST_FAILURE_KEY",
    "finding_to_dict",
    "render_inline_post_failure_json",
    "render_review_json",
    "render_review_output",
    "review_result_to_dict",
    "review_result_to_json",
]


def render_inline_post_failure_json(*, failure: InlinePostFailure) -> str:
    """Serialize an inline-post failure as a one-line machine envelope.

    ``scripts/ci/classify_review_outcome.py`` scans the captured review log
    for this envelope, so a round whose findings only reached the sticky
    comment is never summarized as "P1 findings posted" (#2266).

    Args:
        failure: Findings whose inline comments could not be posted.

    Returns:
        A compact JSON object keyed by :data:`INLINE_POST_FAILURE_KEY`.
    """
    return json.dumps({INLINE_POST_FAILURE_KEY: failure.to_dict()})


def finding_to_dict(*, finding: ReviewFinding) -> dict[str, Any]:
    """Serialize one finding, omitting provenance the run never set.

    ``origin`` (#2269) is present only on a finding the cross-chunk synthesis
    pass produced. Emitting ``"origin": null`` on every other finding would
    change the JSON of every run that never enabled the pass, so the key is
    dropped when unset and rendered as its plain string label when set.

    Args:
        finding: Finding to serialize.

    Returns:
        JSON-serializable mapping for the finding.
    """
    payload = asdict(finding)
    origin = payload.pop("origin", None)
    if origin is not None:
        payload["origin"] = str(origin)
    return payload


def review_result_to_dict(*, result: ReviewResult) -> dict[str, Any]:
    """Convert a review result to a JSON-serializable dictionary.

    The ``timings`` block carries the per-phase breakdown (#2148): ordered
    phase spans plus per-chunk queued/in-flight detail. It is ``None`` when
    the result predates timing instrumentation.

    The top-level ``synthesis`` block reports the optional cross-chunk pass
    (#2269): ``enabled``, ``findings_added``, ``truncated``, and ``failed``.
    ``failed`` is carried explicitly because ``findings_added: 0`` alone
    cannot tell a pass that found nothing from one that could not answer. The
    block is absent entirely when the pass did not run, which is the default.

    The top-level ``findings_coverage_complete`` / ``coverage_degradations`` /
    ``findings_cap_applied`` / ``output_exhaustion_retried`` keys report
    whether the run's finding depth was limited (#2003). A CLI findings cap or
    an output-exhaustion retry is a per-chunk limit; an incomplete cross-chunk
    synthesis pass (#2269) is a whole-run one, and it lands in
    ``coverage_degradations`` and flips ``findings_coverage_complete`` too.
    Only the two per-chunk reasons feed ``findings_cap_applied``, which stays
    ``null`` on a run degraded solely by the synthesis pass. They are always
    present, so a classifier can tell "the model found N issues" from "we
    capped the model at N".

    ``cross_chunk_contradictions`` (#2265) reports how many findings the
    cross-chunk guard tagged for claiming a changed file was never touched
    (P1/P2 among them were moved one band down; a tagged P3 keeps its
    severity), so a consumer can tell a chunk-local claim from a calibrated
    severity. The key is always present, so an unaffected run carries ``0``.

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
    # #2269: same treatment for the synthesis block. It is hoisted to the top
    # level and emitted only when the optional pass actually ran, so a default
    # (disabled) run's payload is byte-identical to one from before the pass
    # existed.
    metadata.pop("synthesis", None)
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
        "findings": [finding_to_dict(finding=finding) for finding in result.findings],
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
        "findings_coverage_complete": result.metadata.findings_coverage_complete,
        "coverage_degradations": degradations,
        # #2265: chunk-local contradictions are downgraded, never dropped, so
        # the count is the only way a consumer can see the guard fired.
        "cross_chunk_contradictions": count_cross_chunk_contradictions(
            findings=result.findings,
        ),
        "findings_cap_applied": result.metadata.findings_cap_applied,
        "output_exhaustion_retried": result.metadata.output_exhaustion_retried,
    }
    if result.metadata.synthesis is not None:
        payload["synthesis"] = result.metadata.synthesis.to_dict()
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
