"""Reasons a review run's finding coverage was degraded (#2003)."""

from __future__ import annotations

from enum import StrEnum, auto


class CoverageDegradationReason(StrEnum):
    """Why a chunk's finding set may be smaller than the diff warranted.

    Neither reason stops the run: every chunk is still reviewed, so this is
    not the same condition as ``ReviewMetadata.partial`` (which means chunks
    went unreviewed). What is lost is *depth* — the model was told to report
    at most N findings, so lower-severity issues beyond the cap may exist and
    go unreported. Recording the reason keeps that from being a silent cap.

    Attributes:
        FINDINGS_CAP_APPLIED: The CLI per-call findings ceiling
            (``ai.cli_max_findings_per_call``) was written into the chunk
            prompt.
        OUTPUT_EXHAUSTION_RETRIED: The chunk call exhausted the provider's
            output-token ceiling and was retried once with a tighter findings
            cap, so that chunk was reviewed under a stricter budget still.
    """

    FINDINGS_CAP_APPLIED = auto()
    OUTPUT_EXHAUSTION_RETRIED = auto()
