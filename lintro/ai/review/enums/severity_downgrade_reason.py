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
    """

    P1_NO_FAILURE_SCENARIO = auto()
    P2_UNEVIDENCED = auto()
