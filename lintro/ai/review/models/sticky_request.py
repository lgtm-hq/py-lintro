"""Everything one review round hands the sticky-comment renderer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState


@dataclass(frozen=True, kw_only=True, slots=True)
class StickyRequest:
    """Inputs for one round of sticky rendering and state advancement.

    The board and the state persisted alongside it are derived from the same
    round, so both entry points take the same request rather than two
    keyword walls that could drift (#2301's parameter ratchet, #2304's
    pipeline convergence).

    Attributes:
        result: Current review result.
        prior_state: Artifact or ledger state carried into this round.
            ``None`` is a first round.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text. ``None`` is an empty map.
        diff_lines: Unused by the renderer; retained because the inline
            posting interface passes it through.
        head_sha: Head commit sha reviewed in this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        cost_basis: Provenance of the reported cost.
        inline_failure: Findings whose inline comments could not be posted.
        inline_comment_ids: Finding key to inline comment id.
        repo: ``owner/name`` slug used to link finding titles to their threads.
        pr_number: Pull request number used for the same links.
        departed_paths: Paths that left the diff this round.
    """

    result: ReviewResult
    prior_state: ReviewState | None = None
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF
    question_map: dict[int, str] | None = None
    diff_lines: dict[str, set[int]] | None = None
    head_sha: str = ""
    transport: str = ""
    auth_mode: str = ""
    cost_basis: str = ""
    inline_failure: InlinePostFailure | None = None
    inline_comment_ids: Mapping[str, int] | None = None
    repo: str = ""
    pr_number: int | None = None
    departed_paths: frozenset[str] | None = None
