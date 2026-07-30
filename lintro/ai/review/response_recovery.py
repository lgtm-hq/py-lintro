"""Recovery of review responses the model did not return as JSON.

A prose answer is not a transport failure: it routinely carries the very
findings the review was asked for. Aborting the chunk with
``kind=invalid_response`` discarded them and truncated the evidence (#1853).

This module supplies the two recovery stages the orchestrator layers on top of
the existing extract-embedded-JSON path:

1. :func:`resolve_schema_retry_timeout` decides whether exactly one
   schema-reminder retry still fits inside the per-call timeout budget.
2. :func:`unstructured_review_payload` turns a still-unparseable answer into a
   review payload carrying the prose verbatim, so the chunk completes with
   unstructured findings instead of failing.
"""

from __future__ import annotations

from typing import Any

from lintro.ai.raw_response import persist_raw_response
from lintro.ai.review.models.review_finding import Severity

__all__ = [
    "MAX_ECHOED_RESPONSE_CHARS",
    "SCHEMA_RETRY_MAX_FRACTION",
    "SCHEMA_RETRY_MIN_TIMEOUT",
    "UNSTRUCTURED_CATEGORY",
    "UNSTRUCTURED_TITLE",
    "build_schema_reminder_prompt",
    "resolve_schema_retry_timeout",
    "unstructured_review_payload",
]

#: A schema-reminder retry is skipped below this many seconds: a call that
#: cannot plausibly finish is worse than falling straight through to the prose
#: fallback, which loses nothing.
SCHEMA_RETRY_MIN_TIMEOUT: float = 30.0

#: Hard ceiling on the retry's share of the per-call timeout. Even when the
#: first call returned instantly, the single retry can never more than double
#: the wall-clock cost of a chunk by half again.
SCHEMA_RETRY_MAX_FRACTION: float = 0.5

#: Cap on how much of the previous answer is echoed back in the reminder
#: prompt. The complete text is always preserved on disk and in the fallback
#: payload; this cap only bounds prompt growth.
MAX_ECHOED_RESPONSE_CHARS: int = 20_000

#: Category and title used for the synthetic finding carrying prose output.
UNSTRUCTURED_CATEGORY = "unstructured-review-output"
UNSTRUCTURED_TITLE = "Unstructured review output (model did not return JSON)"


def resolve_schema_retry_timeout(
    *,
    api_timeout: float,
    elapsed: float,
) -> float | None:
    """Return the timeout for the single schema-reminder retry, or ``None``.

    The retry is charged against the same per-call budget as the call that
    produced the unparseable answer: it gets whatever is left of
    ``api_timeout`` after that call, capped at
    :data:`SCHEMA_RETRY_MAX_FRACTION` of the budget so a fast first call cannot
    turn into a second full-length call. When too little remains, the retry is
    skipped entirely and the caller falls through to the prose fallback.

    Args:
        api_timeout: Configured per-call provider timeout, in seconds.
        elapsed: Wall-clock seconds the first call consumed.

    Returns:
        The retry timeout in seconds, or ``None`` when no retry should be made.
    """
    if api_timeout <= 0:
        return None
    remaining = api_timeout - max(elapsed, 0.0)
    allowance = min(remaining, api_timeout * SCHEMA_RETRY_MAX_FRACTION)
    if allowance < SCHEMA_RETRY_MIN_TIMEOUT:
        return None
    return allowance


def build_schema_reminder_prompt(
    *,
    template: str,
    output_schema: str,
    previous_response: str,
) -> str:
    """Render the schema-reminder prompt for the single retry.

    Args:
        template: The reminder template with ``output_schema`` and
            ``previous_response`` placeholders.
        output_schema: The review output schema text.
        previous_response: The model's unparseable answer.

    Returns:
        The rendered prompt, with the echoed answer capped at
        :data:`MAX_ECHOED_RESPONSE_CHARS`.
    """
    echoed = previous_response
    if len(echoed) > MAX_ECHOED_RESPONSE_CHARS:
        echoed = (
            f"{echoed[:MAX_ECHOED_RESPONSE_CHARS]}\n"
            "[... truncated in this reminder only; the full answer is preserved]"
        )
    return template.format(output_schema=output_schema, previous_response=echoed)


def unstructured_review_payload(
    *,
    content: str,
    files: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a review payload that carries a prose answer verbatim.

    The prose becomes both the chunk summary and a single low-confidence P3
    finding whose description is the complete, untruncated answer. Nothing the
    model produced is dropped, and the chunk completes instead of aborting.

    Args:
        content: The model's complete non-JSON answer.
        files: Files in the chunk; the first is used as the finding location so
            the finding renders against the reviewed code rather than nowhere.

    Returns:
        A payload in the review response shape (``summary``, ``checklist``,
        ``findings``).
    """
    text = content.strip()
    path = persist_raw_response(provider="review", stage="unstructured", raw=content)
    location = f"\n\nFull response saved to {path}." if path is not None else ""
    return {
        "summary": (
            "The model answered in prose instead of the requested JSON. Its "
            "answer is preserved below and reported as an unstructured "
            f"finding.{location}\n\n{text}"
        ),
        "checklist": [],
        "findings": [
            {
                "severity": Severity.P3.value,
                "category": UNSTRUCTURED_CATEGORY,
                "file": files[0] if files else "",
                "line": 0,
                "title": UNSTRUCTURED_TITLE,
                "description": text,
                "cause": (
                    "The response did not parse as the review JSON schema, and "
                    "a schema-reminder retry did not produce one either."
                ),
                "fix": (
                    "Read the preserved answer above: it may contain real "
                    "findings that could not be recorded structurally."
                ),
                "confidence": "low",
                "checklist_ids": [],
            },
        ],
    }
