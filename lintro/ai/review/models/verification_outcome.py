"""What the verification pass did to a round (#2728)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["RefutedFinding", "VerificationSummary"]


@dataclass(frozen=True, slots=True)
class RefutedFinding:
    """One finding the verifier refuted, kept for the record.

    The finding itself leaves the round; what stays is enough to see what was
    dropped and why, on the surfaces' fine print and in the transcript log.

    Attributes:
        file: Path the finding cited.
        line: Line the finding cited.
        severity: Severity the finding claimed.
        title: The finding's title.
        evidence: The verifier's refutation, ``file:line`` and why.
    """

    file: str
    line: int
    severity: str
    title: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON output.

        Returns:
            JSON-serializable mapping.
        """
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class VerificationSummary:
    """What one round's verification pass did.

    Attributes:
        enabled: Whether the pass was configured to run.
        selected: Findings put to the verifier (P1s and low-confidence ones,
            per ``review.verify``).
        confirmed: Findings the verifier could not refute.
        refuted: Findings the verifier refuted, dropped from the round.
        downgraded: P1s whose failure scenario did not hold, moved to P2.
        failed: True when the pass ran but produced no usable answer; the
            selected findings are kept unverified.
        refutations: The refuted findings, for the fine print and the log.
        input_tokens: Prompt tokens the call consumed.
        output_tokens: Completion tokens the call produced.
        cost_estimate: Estimated USD cost of the call.
    """

    enabled: bool = False
    selected: int = 0
    confirmed: int = 0
    refuted: int = 0
    downgraded: int = 0
    failed: bool = False
    refutations: tuple[RefutedFinding, ...] = field(default_factory=tuple)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON output.

        Returns:
            JSON-serializable mapping.
        """
        return {
            "enabled": self.enabled,
            "selected": self.selected,
            "confirmed": self.confirmed,
            "refuted": self.refuted,
            "downgraded": self.downgraded,
            "failed": self.failed,
            "refutations": [item.to_dict() for item in self.refutations],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_estimate": self.cost_estimate,
        }
