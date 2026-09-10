"""Reasons a review run's finding coverage was degraded (#2003)."""

from __future__ import annotations

from enum import StrEnum, auto


class CoverageDegradationReason(StrEnum):
    """Why a run's finding set may be smaller than the diff warranted.

    No reason here stops the run: every chunk is still reviewed, so this is
    not the same condition as ``ReviewMetadata.partial`` (which means chunks
    went unreviewed). What is lost is *depth*, and it is lost at one of two
    scopes. A **per-chunk** reason means either that one chunk's model call was
    told to report at most N findings, so issues past that ceiling may exist in
    that chunk and go unreported, or that one of that chunk's optional deeper
    (depth >= 2) passes failed, so the chunk keeps its main-pass result and
    whatever the extra pass would have added is missing (#2395). A
    **whole-run** reason means an optional extra sweep over the merged result
    ran short or not at all, so the issues only that sweep could have caught
    may exist anywhere in the diff and go unreported. A whole-run reason carries
    :data:`~lintro.ai.review.models.coverage_degradation.SYNTHESIS_CHUNK_INDEX`
    rather than a real chunk index, and a placeholder ``findings_cap`` that is
    never a real per-call ceiling. Recording the reason keeps either gap from
    being silent.

    Attributes:
        FINDINGS_CAP_APPLIED: A chunk's answer reached the CLI per-call
            findings ceiling (``ai.cli_max_findings_per_call``), so the model
            was told to stop there and summarize the overflow. Recorded on the
            parsed finding count, never on the mere presence of a configured
            ceiling (#2283).
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
        GENERATED_QUESTIONS_FAILED: The depth-2 generated-questions call for
            one chunk failed, so that chunk was reviewed against the static
            checklist alone (#2395).
        ADVERSARIAL_SWEEP_FAILED: The depth-3 adversarial sweep for one chunk
            failed, so the chunk keeps its main-pass findings and whatever the
            sweep would have added is missing (#2395).
    """

    FINDINGS_CAP_APPLIED = auto()
    OUTPUT_EXHAUSTION_RETRIED = auto()
    SYNTHESIS_TRUNCATED = auto()
    SYNTHESIS_FAILED = auto()
    GENERATED_QUESTIONS_FAILED = auto()
    ADVERSARIAL_SWEEP_FAILED = auto()
