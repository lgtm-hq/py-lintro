"""Outcome of the cross-chunk synthesis pass (#2269)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["SynthesisOutcome"]


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    """What the final cross-chunk synthesis pass did on one run.

    The outcome exists only when the pass actually ran, so every surface can
    treat ``ReviewMetadata.synthesis is None`` as "this run had no synthesis
    pass" and render nothing at all. That keeps a default (disabled) run's
    output byte-identical to one from before the pass existed.

    Attributes:
        findings_added: Number of synthesized findings that survived the cap,
            the severity gate, and deduplication against the chunk findings.
        truncated: True when the whole-PR diff did not fit the pass's token
            budget, so it reasoned over a subset of the changed files.
        failed: True when the pass was attempted but produced no usable
            answer. Never fatal: the chunk findings stand and the run stays
            complete for them.
    """

    findings_added: int = 0
    truncated: bool = False
    failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the outcome for the review JSON payload.

        Returns:
            The ``synthesis`` block: ``enabled`` is always ``True`` because
            the block is emitted only when the pass ran, alongside the number
            of findings it contributed and whether its input was truncated.
        """
        return {
            "enabled": True,
            "findings_added": self.findings_added,
            "truncated": self.truncated,
        }
