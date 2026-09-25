"""The per-PR questions one review run's chunks share (#2720, #2813)."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.question_failure_kind import QuestionFailureKind
from lintro.ai.review.merge import ChunkReviewPartial

__all__ = ["RunQuestions"]


@dataclass(frozen=True, slots=True)
class RunQuestions:
    """The per-PR questions one run's chunks share.

    Attributes:
        text: Rendered "consider" items (``G1. …``), empty when none.
        count: Number of questions rendered.
        diff_trimmed: True when the whole-PR diff did not fit the budget and
            the generator saw a prefix of its files.
        files_seen: Files whose diff the generator saw.
        files_total: Files in the PR diff.
        failed: True when the call or its answer was unusable; the run then
            reviews with the rubric alone and records the degradation.
        usage: Token and cost usage of the generator call (both attempts'
            when the pass was retried).
        failure_kind: Why a failed pass failed (#2813); None when it did not.
            After a failed retry it is the first attempt's kind.
        retried: True when the pass made its one retry (#2813).
        capture: The failed answer's start, redacted and JSON-encoded onto one
            line, for the log only; empty when there was no answer.
    """

    text: str = ""
    count: int = 0
    diff_trimmed: bool = False
    files_seen: int = 0
    files_total: int = 0
    failed: bool = False
    usage: ChunkReviewPartial = ChunkReviewPartial(
        findings=(),
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
    )
    failure_kind: QuestionFailureKind | None = None
    retried: bool = False
    capture: str = ""

    @property
    def lines(self) -> tuple[str, ...]:
        """The rendered questions, one per line, for the run record."""
        return tuple(self.text.splitlines())
