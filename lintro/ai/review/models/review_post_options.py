"""How a review round renders and behaves when it posts to GitHub (#2305)."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_state import ReviewState

__all__ = ["ReviewPostOptions"]


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewPostOptions:
    """Everything about a post that is not *where* it goes.

    The pull request a round posts to is three values — a reporter, or the
    number and repository to build one from — and they stay on the call. What
    the round renders and what the lifecycle is allowed to do travel here, so
    the posting entry point keeps a signature a reader can hold in their head
    and stays inside the eight-parameter ratchet (#2301).

    Attributes:
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        transport: Provider transport used for this round (e.g. ``cli``).
        auth_mode: Authentication mode used by the transport.
        cost_basis: Provenance of the reported cost (``billed`` /
            ``estimated`` / ``unpriceable``).
        config_source: Human-readable description of where this run's settings
            came from, shown under the review body's run stats.
        auto_resolve: ``review.auto_resolve``. When false, an addressed thread
            still gets its banner but is left for a human to resolve.
        prior_state: Artifact or ledger state this round continues from. When
            it carries nothing, the state left on the sticky comment is used.
        departed_paths: Paths that left the diff this round (deletes and
            rename sources). Their open findings may resolve.
        captured_comment_ids: Optional sink for newly captured inline comment
            ids so the caller can persist them after posting.
    """

    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF
    question_map: dict[int, str] | None = None
    transport: str = ""
    auth_mode: str = ""
    cost_basis: str = ""
    config_source: str = ""
    auto_resolve: bool = True
    prior_state: ReviewState | None = None
    departed_paths: frozenset[str] | None = None
    captured_comment_ids: dict[str, int] | None = None
