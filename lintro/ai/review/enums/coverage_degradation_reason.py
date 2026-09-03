"""Reasons a review run's finding coverage was degraded (#2003)."""

from __future__ import annotations

from enum import StrEnum, auto


class CoverageDegradationReason(StrEnum):
    """Why a chunk's finding set may be smaller than the diff warranted.

    No reason here stops the run: every chunk is still reviewed, so this is
    not the same condition as ``ReviewMetadata.partial`` (which means chunks
    went unreviewed). What is lost is *depth* — the model was told to report
    at most N findings, or an optional extra sweep did not complete, so
    issues that pass would have caught may exist and go unreported.
    Recording the reason keeps that from being a silent gap.

    Attributes:
        FINDINGS_CAP_APPLIED: The CLI per-call findings ceiling
            (``ai.cli_max_findings_per_call``) was written into the chunk
            prompt.
        OUTPUT_EXHAUSTION_RETRIED: The chunk call exhausted the provider's
            output-token ceiling and was retried once with a tighter findings
            cap, so that chunk was reviewed under a stricter budget still.
        SYNTHESIS_TRUNCATED: The cross-chunk synthesis pass (#2269) ran, but
            the whole-PR diff did not fit its token budget, so it reasoned
            over a subset of the changed files.
        SYNTHESIS_FAILED: The cross-chunk synthesis pass was enabled and
            attempted but did not produce a usable answer. The chunk findings
            are unaffected and the run stays complete for them; only the
            cross-file sweep is missing.
    """

    FINDINGS_CAP_APPLIED = auto()
    OUTPUT_EXHAUSTION_RETRIED = auto()
    SYNTHESIS_TRUNCATED = auto()
    SYNTHESIS_FAILED = auto()
