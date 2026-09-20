"""Why a mechanical severity gate lowered a finding's reported severity."""

from __future__ import annotations

from enum import StrEnum, auto


class SeverityDowngradeReason(StrEnum):
    """Which evidence gate rewrote a finding's severity, and why (#1925, #2723).

    Both gates are mechanical rather than judgement calls, and both record
    the rewrite on the finding so every surface renders it instead of
    presenting the gated severity as the model's own.

    Attributes:
        P1_NO_FAILURE_SCENARIO: A P1 reported without a concrete
            ``failure_scenario`` was moved to P2 (#1925).
        P2_UNEVIDENCED: A P2 in a category that needs diff-local evidence
            to flip the verdict — ``test-gap``, ``contract-drift`` or
            ``code-smell`` — whose ``evidence_style`` was not ``diff_local``
            was moved to P3 (#2723): a coverage or wording concern the model
            inferred rather than showed must not turn "nits only" into
            "changes requested".
        P1_THEN_P2_UNEVIDENCED: Both gates fired on one finding: reported
            P1 without a failure scenario in a gated category without
            diff-local evidence, so it went P1 → P2 → P3. Kept as its own
            member so the counts and the notice credit both gates.
        REFUTATION_WEAKENED: The verification pass (#2728) found the defect
            real but its failure scenario not holding at P1, so the finding
            was moved to P2 before the mechanical gates ran.
    """

    P1_NO_FAILURE_SCENARIO = auto()
    P2_UNEVIDENCED = auto()
    P1_THEN_P2_UNEVIDENCED = auto()
    REFUTATION_WEAKENED = auto()

    @property
    def p1_gate_fired(self) -> bool:
        """Whether the P1 failure-scenario gate contributed to this reason."""
        return self in {
            SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
            SeverityDowngradeReason.P1_THEN_P2_UNEVIDENCED,
        }

    @property
    def p2_gate_fired(self) -> bool:
        """Whether the P2 evidence gate contributed to this reason."""
        return self in {
            SeverityDowngradeReason.P2_UNEVIDENCED,
            SeverityDowngradeReason.P1_THEN_P2_UNEVIDENCED,
        }
