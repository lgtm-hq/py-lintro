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

Only self-classifying statements count. A phrase that merely occurs inside
defect prose ("treats HTTP 200 as a confirmation of rollback success") or
inside a longer fix instruction ("Update the README; no code change needed")
is not a classification, and a finding carrying one is kept.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.response_recovery import UNSTRUCTURED_CATEGORY

if TYPE_CHECKING:
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "CONFIRMATION_FIX_PATTERN",
    "CONFIRMATION_SENTENCE_PATTERN",
    "drop_confirmation_findings",
    "is_confirmation_finding",
]

#: A ``fix`` whose whole text (stripped, case-folded) is a bare "no code
#: change" or "none". Anchored at both ends on purpose: a fix that goes on to
#: describe work is an instruction, not a classification.
CONFIRMATION_FIX_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:no code change(?: (?:needed|required))?|none)\.?$",
)

#: A ``description`` sentence that opens by classifying the finding as a
#: confirmation: "Not a defect.", "This is a confirmation that ...", or the
#: checklist-mapped form the prompt used to elicit, "Checklist item 8 is a
#: positive verification, ...". The alternation is anchored at start-of-text
#: or after sentence punctuation, so the same words mid-sentence do not match.
#: "confirmation" and "positive verification" also need the "this is" /
#: "checklist item N is" subject and must end the clause (or continue with
#: "that"/"of"), so defect prose such as "Confirmation emails are never sent"
#: or "This is a confirmation dialog that never opens" is kept.
CONFIRMATION_SENTENCE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|[.!?]\s+)"
    r"(?:"
    r"(?:this|checklist item \d+) is (?:a |an )?"
    r"(?:positive verification|confirmation)"
    r"(?=\s+(?:that|of)\b|\s*[.,;:]|\s*$)"
    r"|(?:(?:this|checklist item \d+) is )?not a defect(?=\s*[.,;:]|\s|$)"
    r")",
    re.IGNORECASE,
)


def is_confirmation_finding(*, finding: ReviewFinding) -> bool:
    """Return whether a finding classifies itself as not a defect.

    The prose-recovery finding (:data:`UNSTRUCTURED_CATEGORY`) is never a
    confirmation: its description is the model's whole answer, which may
    open with such a sentence while carrying real findings further down.

    Args:
        finding: A parsed finding from one chunk answer.

    Returns:
        True when the whole fix matches :data:`CONFIRMATION_FIX_PATTERN` or a
        description sentence opens as :data:`CONFIRMATION_SENTENCE_PATTERN`.
    """
    if finding.category == UNSTRUCTURED_CATEGORY:
        return False
    if CONFIRMATION_FIX_PATTERN.match(finding.fix.strip().casefold()):
        return True
    return CONFIRMATION_SENTENCE_PATTERN.search(finding.description.strip()) is not None


def drop_confirmation_findings(
    *,
    findings: tuple[ReviewFinding, ...],
) -> tuple[ReviewFinding, ...]:
    """Drop findings that classify themselves as confirmations, not defects.

    Each drop is logged at info level with the finding title so it is visible
    in the review run log. Severity and every other field of the kept
    findings are left untouched.

    Args:
        findings: Parsed findings from one chunk answer, in payload order.

    Returns:
        The findings that describe an actual defect, in their original order.
    """
    kept: list[ReviewFinding] = []
    for finding in findings:
        if is_confirmation_finding(finding=finding):
            logger.info(
                "Dropped checklist-confirmation finding {title!r}: its body says "
                "it is not a defect (#2430)",
                title=finding.title,
            )
            continue
        kept.append(finding)
    return tuple(kept)
