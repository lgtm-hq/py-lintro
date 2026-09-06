"""The resolved inputs one assembled sticky body renders from."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.run_record import RunRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class StickyPlan:
    """What every sticky section renderer may read, resolved once per body.

    A body rendered from a completed round and one re-rendered from persisted
    state alone differ in exactly one field — ``result`` is ``None`` for the
    latter — so both go through the same plan and the same section renderers
    (#1954, #2304).

    Attributes:
        match: Cross-round matching outcome for the rendered round.
        verdict: Readiness verdict, including the coverage gate.
        round_number: 1-based round number being rendered.
        result: Current review result, or ``None`` on a state-only re-render.
        head_sha: Head commit sha reviewed in that round.
        runs: Every retained run record, oldest first, current run last.
        transport: Provider transport used for the round.
        auth_mode: Authentication mode used by the transport.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text.
        inline_failure: Findings whose inline comments could not be posted.
        repo: ``owner/name`` slug used to link finding titles.
        pr_number: Pull request number used for the same links.
    """

    match: FindingMatchResult
    verdict: ReviewVerdict
    round_number: int
    result: ReviewResult | None = None
    head_sha: str = ""
    runs: tuple[RunRecord, ...] = ()
    transport: str = ""
    auth_mode: str = ""
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF
    question_map: dict[int, str] = field(default_factory=dict)
    inline_failure: InlinePostFailure | None = None
    repo: str = ""
    pr_number: int | None = None
