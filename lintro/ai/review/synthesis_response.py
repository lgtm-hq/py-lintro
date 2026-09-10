"""Reading the cross-chunk synthesis response (#2269).

Split out of :mod:`lintro.ai.review.synthesis` (#2301): that module owns the
pass — when it runs, the one provider call, and how a failure degrades — while
turning the answer into findings lives here. Both functions were moved
verbatim, so the pass's fail-soft contract is unchanged: a response that cannot
be read comes back as ``None`` and never as an empty success.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from loguru import logger

from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.finding_matcher import fingerprint_for
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "deduplicate_synthesis_findings",
    "parse_synthesis_findings",
]


def parse_synthesis_findings(*, content: str) -> tuple[ReviewFinding, ...] | None:
    """Parse the pass's response with the shared finding parser.

    Args:
        content: Raw model response text.

    Returns:
        Parsed findings, or ``None`` when the response was not a JSON object
        carrying a ``findings`` list — a missing key counts the same as a
        malformed value. The caller records that as a failed pass rather than
        an empty one, so "found nothing" and "could not be read" never look
        alike. Only a present, empty list is an empty success.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse the cross-chunk synthesis response as JSON.")
        return None
    if not isinstance(payload, dict):
        logger.warning("The cross-chunk synthesis payload was not an object.")
        return None
    if "findings" not in payload:
        # An answer that never mentions ``findings`` did not answer. Defaulting
        # it to an empty list would report "found no cross-file
        # inconsistencies" for a call that produced nothing usable.
        logger.warning("The cross-chunk synthesis payload had no findings key.")
        return None
    raw = payload["findings"]
    if not isinstance(raw, list):
        # ``parse_findings`` would quietly render a string, a mapping, or a
        # null here as no findings at all, which is exactly the "empty
        # success" this pass promises never to confuse with a failure.
        logger.warning("The cross-chunk synthesis findings value was not a list.")
        return None
    return parse_findings(raw_findings=raw)


def deduplicate_synthesis_findings(
    *,
    candidates: Sequence[ReviewFinding],
    existing: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Drop synthesized findings the chunk passes already reported.

    Identity is the cross-round fingerprint the state ledger already uses, so
    a synthesized restatement collapses onto the chunk finding it duplicates
    instead of appearing beside it under a slightly different line number.

    Args:
        candidates: Synthesized findings, in reported order.
        existing: Findings already merged from the chunk passes.

    Returns:
        The candidates whose fingerprint is new, in reported order.
    """
    seen = {
        fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        for finding in existing
    }
    kept: list[ReviewFinding] = []
    for finding in candidates:
        fingerprint = fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        if fingerprint in seen:
            logger.debug(
                "Dropping synthesized finding {title!r}: already reported.",
                title=finding.title,
            )
            continue
        seen.add(fingerprint)
        kept.append(finding)
    return tuple(kept)
