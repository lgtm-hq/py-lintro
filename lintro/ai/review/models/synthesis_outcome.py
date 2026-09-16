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
            ``summary`` or no usable ``verdict_reasoning``, so the round
            renders the TL;DR-only fallback for whichever is missing. The
            findings half of the answer still counts; this flag keeps a
            narrative-less round from reading as a fully successful pass.
        input_tokens: Prompt tokens the call consumed, as the provider
            reported them (#2702).
        output_tokens: Completion tokens the call produced.
        output_limit_tokens: The output ceiling the call ran under:
            ``ai.max_tokens`` on the API transport, ``None`` on the CLI
            transport, whose only ceiling is the agent's own.
        input_budget_tokens: The input budget the prompt was fitted to
            (``ai.review_synthesis_diff_tokens`` after the context-window
            clamp).
        prompt_tokens_estimated: Estimated size of the fitted prompt.
        diff_files_included: Changed files whose diff reached the prompt.
        diff_files_total: Changed files in the PR's diff.
    """

    findings_added: int = 0
    truncated: bool = False
    failed: bool = False
    duplicates_merged: int = 0
    narrative_missing: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    output_limit_tokens: int | None = None
    input_budget_tokens: int = 0
    prompt_tokens_estimated: int = 0
    diff_files_included: int = 0
    diff_files_total: int = 0

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
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "output_limit_tokens": self.output_limit_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "prompt_tokens_estimated": self.prompt_tokens_estimated,
            "diff_files_included": self.diff_files_included,
            "diff_files_total": self.diff_files_total,
        }
