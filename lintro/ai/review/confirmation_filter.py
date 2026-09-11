"""Parse-time drop of checklist confirmations that arrived as findings (#2430).

The review prompt used to require a finding for every checklist "yes", so a
reviewer that verified a change as correct still emitted a finding — one whose
own body said "positive verification, not a defect" with a fix of "No code
change" (seen on lgtm-hq/homebrew-tap#411, where it opened an inline thread a
human had to resolve before the release could merge). The prompt rule is gone;
this module is the backstop for a model that still produces one. It runs in
:func:`lintro.ai.review.response_pipeline.payload_to_partial`, before the
caller counts the answer against its findings cap, so a dropped confirmation
never counts as a capped-out finding.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "CONFIRMATION_PHRASES",
    "drop_confirmation_findings",
    "is_confirmation_finding",
]

#: Phrases whose presence in a finding's description or fix marks it as a
#: checklist confirmation rather than a defect. Matched case-insensitively on
#: word boundaries, so ``unconfirmed`` does not match ``confirmation``.
CONFIRMATION_PHRASES: tuple[str, ...] = (
    "not a defect",
    "no code change",
    "positive verification",
    "this is a confirmation",
    "confirmation",
)

#: ``fix`` text that on its own marks a finding as a confirmation, compared
#: case-insensitively after stripping surrounding whitespace.
_NO_CODE_CHANGE_FIX: str = "no code change"

_CONFIRMATION_PATTERN: re.Pattern[str] = re.compile(
    "|".join(rf"\b{re.escape(phrase)}\b" for phrase in CONFIRMATION_PHRASES),
    re.IGNORECASE,
)


def is_confirmation_finding(*, finding: ReviewFinding) -> bool:
    """Return whether a finding's own text says it is not a defect.

    Args:
        finding: A parsed finding from one chunk answer.

    Returns:
        True when the description or fix contains one of
        :data:`CONFIRMATION_PHRASES` (case-insensitive) or the fix is exactly
        "No code change".
    """
    if finding.fix.strip().casefold() == _NO_CODE_CHANGE_FIX:
        return True
    return any(
        _CONFIRMATION_PATTERN.search(text)
        for text in (finding.description, finding.fix)
    )


def drop_confirmation_findings(
    *,
    findings: tuple[ReviewFinding, ...],
) -> tuple[ReviewFinding, ...]:
    """Drop findings that describe a checklist confirmation, not a defect.

    Each drop is logged at debug level with the finding title. Severity and
    every other field of the kept findings are left untouched.

    Args:
        findings: Parsed findings from one chunk answer, in payload order.

    Returns:
        The findings that describe an actual defect, in their original order.
    """
    kept: list[ReviewFinding] = []
    for finding in findings:
        if is_confirmation_finding(finding=finding):
            logger.debug(
                "Dropped checklist-confirmation finding {title!r}: its body says "
                "it is not a defect (#2430)",
                title=finding.title,
            )
            continue
        kept.append(finding)
    return tuple(kept)
