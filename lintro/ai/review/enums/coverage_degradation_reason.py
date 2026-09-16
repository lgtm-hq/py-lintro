"""Reasons a review run's finding coverage was degraded (#2003)."""

from __future__ import annotations

from enum import StrEnum, auto


class CoverageDegradationReason(StrEnum):
    """Why a run's finding set may be smaller than the diff warranted.

    No reason here stops the run, and none is the same condition as
    ``ReviewMetadata.partial`` (which means whole chunks went unreviewed).
    What is usually lost is *depth*, at one of two scopes. Two reasons also
    cost coverage: ``DIFF_TRUNCATED`` leaves part of one file's diff unread,
    and ``SPLIT_HALF_FAILED`` leaves the failed half's files unreviewed.
    A **per-chunk** reason means one chunk's answer had to be re-obtained
    under different conditions (an oversized answer split the chunk and each
    half was reviewed on its own) or that one of that chunk's optional deeper
    (depth >= 2) passes failed, so the chunk keeps its main-pass result and
    whatever the extra pass would have added is missing (#2395). A
    **whole-run** reason means an optional extra sweep over the merged result
    ran short or not at all, so the issues only that sweep could have caught
    may exist anywhere in the diff and go unreported. A whole-run reason
    carries
    :data:`~lintro.ai.review.models.coverage_degradation.SYNTHESIS_CHUNK_INDEX`
    rather than a real chunk index. Recording the reason keeps either gap from
    being silent.

    No reason exists for a per-call findings cap: there is none (lintro-ops
    milestone 0, decision A). A chunk reports every finding it has.

    Attributes:
        OUTPUT_EXHAUSTION_RETRIED: The chunk call exhausted the provider's
            output-token ceiling. The chunk was split by file into two halves
            that were each reviewed once (or, for a single-file chunk, the
            call was retried once unchanged), so findings that only a
            whole-chunk view would have surfaced may be missing.
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
        DIFF_TRUNCATED: One file's diff exceeded the hard per-chunk ceiling
            (the context-window remainder) and was cut to fit, so the model
            reviewed only a prefix of that file's change and findings past
            the cut may go unreported. The file is credited as covered so
            the review converges; its coverage record carries the truncation
            and the reason is re-recorded (with a carried sentinel index) on
            every later round that skips the file, until its diff changes.
        SPLIT_HALF_FAILED: After an output-exhaustion split, one half's call
            failed while the other completed. The surviving half's findings
            are kept and the files in the failed half are left unreviewed
            (they are not credited as covered), so this run did not review
            every file it started.
    """

    OUTPUT_EXHAUSTION_RETRIED = auto()
    SYNTHESIS_TRUNCATED = auto()
    SYNTHESIS_FAILED = auto()
    GENERATED_QUESTIONS_FAILED = auto()
    ADVERSARIAL_SWEEP_FAILED = auto()
    DIFF_TRUNCATED = auto()
    SPLIT_HALF_FAILED = auto()


#: Degradations of the whole-PR narrative pass, not of per-file findings
#: coverage (#2702). They stay in ``coverage_degradations`` for the record and
#: the synthesis note, but never make a review "partial".
NARRATIVE_DEGRADATION_REASONS: frozenset[CoverageDegradationReason] = frozenset(
    {
        CoverageDegradationReason.SYNTHESIS_TRUNCATED,
        CoverageDegradationReason.SYNTHESIS_FAILED,
    },
)
