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
    pass" and render nothing at all. The pass is on by default (lintro-ops
    milestone 0, decision A): it writes the round's summary and verdict
    reasoning, merges duplicate findings and adds cross-file findings.

    Attributes:
        findings_added: Number of synthesized findings that survived the cap,
            the severity gate, and deduplication against the chunk findings.
        truncated: True when the whole-PR diff did not fit the pass's token
            budget, so it reasoned over a subset of the changed files.
        failed: True when the pass was attempted but produced no usable
            answer. Never fatal: the chunk findings stand and the run stays
            complete for them.
        duplicates_merged: Number of chunk findings the pass collapsed into
            another finding with the same root cause (lintro-ops milestone 0).
        narrative_missing: True when the pass answered but wrote no usable
            ``summary``, so the round renders the TL;DR-only fallback. The
            findings half of the answer still counts; this flag keeps a
            summary-less round from reading as a fully successful pass.
    """

    findings_added: int = 0
    truncated: bool = False
    failed: bool = False
    duplicates_merged: int = 0
    narrative_missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the outcome for the review JSON payload.

        Returns:
            The ``synthesis`` block: ``enabled`` is always ``True`` because
            the block is emitted only when the pass ran, alongside the number
            of findings it contributed, whether its input was truncated, and
            whether it failed. ``failed`` is carried explicitly because
            ``findings_added: 0`` alone cannot tell a pass that found nothing
            from one that could not answer, and a consumer must not have to
            cross-reference ``coverage_degradations`` to tell them apart.
        """
        return {
            "enabled": True,
            "findings_added": self.findings_added,
            "truncated": self.truncated,
            "failed": self.failed,
            "duplicates_merged": self.duplicates_merged,
            "narrative_missing": self.narrative_missing,
        }
