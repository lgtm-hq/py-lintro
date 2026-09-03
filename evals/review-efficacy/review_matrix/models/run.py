"""Model for one persisted ``lintro review`` invocation."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_finding import ReviewFinding
from review_matrix.enums.run_status import RunStatus

__all__ = ["EvalRun"]


@dataclass(frozen=True, slots=True)
class EvalRun:
    """One repeated review of one corpus item under one matrix config.

    Attributes:
        config_id: Matrix config that produced the run.
        item_id: Corpus item that was reviewed.
        repeat: 1-based repeat index within the (config, item) cell.
        status: Whether the run produced comparable findings.
        verdict: Verdict derived in code from the run's findings via
            :func:`lintro.ai.review.finding_matcher.derive_verdict`, never the
            label the model wrote. ``None`` when the run produced no findings
            payload at all.
        findings: Findings reported by the run, in payload order.
        elapsed_seconds: Wall-clock duration of the invocation. This is the
            only non-deterministic value in a report.
        cost_usd: ``metadata.cost_estimate_usd`` from the review payload.
        exit_code: Process exit code, or ``-1`` when the invocation never ran.
        error: Diagnostic for a failed or unparseable run; empty when ``OK``.
        output_path: Path of the persisted raw payload, relative to the run
            directory.
    """

    config_id: str
    item_id: str
    repeat: int
    status: RunStatus
    verdict: ReviewVerdict | None = None
    findings: tuple[ReviewFinding, ...] = field(default_factory=tuple)
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0
    exit_code: int = -1
    error: str = ""
    output_path: str = ""

    @property
    def is_comparable(self) -> bool:
        """Return True when the run can take part in a metric."""
        return self.status is RunStatus.OK and self.verdict is not None
