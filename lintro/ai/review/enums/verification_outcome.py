"""What the verification pass decided about one finding (#2728)."""

from __future__ import annotations

from enum import StrEnum, auto


class VerificationOutcome(StrEnum):
    """The verifier's answer for one selected finding.

    Attributes:
        CONFIRMED: The verifier tried to refute the finding and could not;
            it is kept and marked verified.
        REFUTED: The verifier showed, with ``file:line`` evidence in the
            material it was given, why the described failure cannot happen;
            the finding is dropped from the round and the refutation is
            recorded.
        DOWNGRADED: The defect is real but the failure scenario did not hold
            at the claimed severity; a P1 is moved to P2 and marked with
            :attr:`~lintro.ai.review.enums.severity_downgrade_reason.SeverityDowngradeReason.REFUTATION_WEAKENED`.
    """

    CONFIRMED = auto()
    REFUTED = auto()
    DOWNGRADED = auto()
