"""What one round's moved-finding verification decided (#3466)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["MovedVerificationSummary", "MovedVerdictEntry"]

#: ``(key, verdict, path, line, line_sha)`` for one verified prior finding.
#: ``verdict`` is a :class:`~lintro.ai.review.enums.moved_verdict.MovedVerdict`
#: value; ``path``, ``line`` and ``line_sha`` name the location a ``present``
#: answer moved the finding to, and are ``""``, ``0`` and ``""`` otherwise.
MovedVerdictEntry = tuple[str, str, str, int, str]


@dataclass(frozen=True, slots=True)
class MovedVerificationSummary:
    """Counts for the round's prior findings whose location was inferred.

    A prior open finding no report matched, placed this round by anything
    other than an exact in-place mapping (a followed or weak rename, a
    delete, a held location, a lineage), is resolved only when the provider
    says the issue is gone. These counts say what happened to those
    findings; every one of them is in exactly one of ``verified_fixed``,
    ``verified_present`` and ``held``.

    Attributes:
        enabled: Whether ``review.verify_moved_findings`` allowed the call.
        candidates: Prior findings that needed the check this round.
        verified_fixed: Findings the provider said are gone; resolved.
        verified_present: Findings the provider said are still present;
            kept open.
        moved: The subset of ``verified_present`` moved to the location the
            provider named, inside the diff.
        held: Findings with no usable answer, held as "not re-verified".
        failed: True when the call was made and produced no usable answer.
        input_tokens: Prompt tokens the call consumed.
        output_tokens: Completion tokens the call produced.
        cost_estimate: Estimated USD cost of the call.
    """

    enabled: bool = True
    candidates: int = 0
    verified_fixed: int = 0
    verified_present: int = 0
    moved: int = 0
    held: int = 0
    failed: bool = False
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
            "candidates": self.candidates,
            "verified_fixed": self.verified_fixed,
            "verified_present": self.verified_present,
            "moved": self.moved,
            "held": self.held,
            "failed": self.failed,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_estimate": self.cost_estimate,
        }
