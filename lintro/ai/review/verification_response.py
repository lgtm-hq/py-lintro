"""Answer parsing for the verification pass (#2728).

The verifier answers one JSON object keyed by finding position; an unknown
outcome label or an out-of-range position is skipped so a malformed entry
costs one finding its verdict, not the round its pass.
"""

from __future__ import annotations

import json
import re

from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.enums.verification_outcome import VerificationOutcome

__all__ = ["parse_verification_answer"]

#: A ``path:line`` citation somewhere in the evidence text.
_CITATION = re.compile(r"[\w./\\-]+\.\w+:\d+")


def parse_verification_answer(
    *,
    content: str,
    count: int,
) -> dict[int, tuple[VerificationOutcome, str]] | None:
    """Parse the verifier's answer into ``position -> (outcome, evidence)``.

    Args:
        content: The raw answer text.
        count: How many findings were put to the verifier.

    Returns:
        The parsed verdicts keyed by 1-based position, or ``None`` when the
        answer is not the JSON shape asked for. An unknown outcome label or
        an out-of-range position is skipped; a finding with no verdict stays
        unverified.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("verifications")
    if not isinstance(items, list):
        return None
    verdicts: dict[int, tuple[VerificationOutcome, str]] = {}
    labels = {
        "refuted": VerificationOutcome.REFUTED,
        "weakened": VerificationOutcome.DOWNGRADED,
        "unrefuted": VerificationOutcome.CONFIRMED,
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        position = item.get("index")
        outcome = labels.get(str(item.get("outcome", "")).strip().lower())
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not 1 <= position <= count
            or outcome is None
        ):
            continue
        evidence = item.get("evidence")
        evidence = evidence.strip() if isinstance(evidence, str) else ""
        if outcome is VerificationOutcome.REFUTED and not _CITATION.search(evidence):
            # The prompt's first rule: a refutation without a ``file:line``
            # citation is no refutation. Emptying the evidence makes the
            # application keep the finding as confirmed.
            evidence = ""
        verdicts.setdefault(position, (outcome, evidence))
    return verdicts
