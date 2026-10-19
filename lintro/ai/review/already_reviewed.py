"""Skip a round whose head the last finished round already covered (#2338).

A push that does not change the reviewed head, or an in-place "Re-run failed
jobs", used to cost a near-full round: every file carrying an open finding is
re-read on each round (:func:`lintro.ai.review.delta.open_thread_paths`,
#2627), and the question pass and synthesis run whenever a round runs. On the
same head the code those threads point at has not changed, so a re-read can
only differ by model nondeterminism. ADR-0007 promises that a quiet re-review
makes zero provider calls; this module is what makes that true when findings
are open.

The decision is taken after the state is loaded and before any provider is
constructed, next to the convergence stop rule (#2099), and mirrors its
shape: no provider call, no state write, the sticky untouched, a
machine-readable envelope with ``outcome: already_reviewed``. ``--full`` and
``LINTRO_REVIEW_FORCE`` force a round, as the on-request review of #2627 will.

State is persisted before the posting step runs, so under ``--post`` the
decision also needs publication evidence: the sticky exists and every open
finding the posting policy would have posted inline carries its thread id.
Without that evidence the round runs, so a run whose posting step failed
posts on retry.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lintro.ai.review.degradation_carry import latest_degradations, latest_finished_run
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_constants import STICKY_MARKER
from lintro.ai.review.posting_policy import PostingPolicy, coerce_confidence
from lintro.ai.review.posting_tiers import INLINE_SEVERITIES
from lintro.ai.review.resume import plan_resume

if TYPE_CHECKING:
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "FORCE_ENV",
    "AlreadyReviewed",
    "evaluate_already_reviewed",
    "force_requested",
    "publication_complete",
]

#: Environment override that forces a round past this guard, for a rerun that
#: must review again whatever the state says (an on-request review, #2627).
FORCE_ENV = "LINTRO_REVIEW_FORCE"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Classifications that owe no provider call on an unchanged head. An open
#: thread is re-read on a new head so the model can confirm a fix; on the
#: same head there is nothing new to read.
_QUIET_NEEDS = frozenset({FileReviewNeed.COVERED, FileReviewNeed.OPEN_THREAD})


@dataclass(frozen=True, slots=True)
class AlreadyReviewed:
    """Why this round is skipped: the head is covered by a finished round.

    Attributes:
        head_sha: The head both the last finished round and this run review.
        round: The round that covered it.
        files_covered: Eligible files, every one covered at this head.
        open_findings: Open finding records carried as they are.
    """

    head_sha: str
    round: int
    files_covered: int
    open_findings: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON envelope.

        Returns:
            A JSON-serializable mapping of the fields.
        """
        return {
            "head_sha": self.head_sha,
            "round": self.round,
            "files_covered": self.files_covered,
            "open_findings": self.open_findings,
        }

    def describe(self) -> str:
        """Word the decision for the terminal, the envelope and CI.

        Returns:
            One line naming the head, the round and what is carried.
        """
        noun = "finding" if self.open_findings == 1 else "findings"
        return (
            f"already reviewed at {self.head_sha[:7]} in round {self.round}: "
            f"{self.files_covered} files covered, {self.open_findings} open "
            f"{noun} carried"
        )


def force_requested(*, env: Mapping[str, str] | None = None) -> bool:
    """Return whether :data:`FORCE_ENV` asks for a round regardless.

    Args:
        env: Environment to read; the process environment when ``None``.

    Returns:
        True for ``1``, ``true``, ``yes`` or ``on``, case-insensitively.
    """
    environment: Mapping[str, str] = os.environ if env is None else env
    return environment.get(FORCE_ENV, "").strip().lower() in _TRUTHY


def evaluate_already_reviewed(
    *,
    context: ReviewContext,
    prior: ReviewState | None,
    force_full: bool,
    reusable_rounds: Collection[int] | None,
    env: Mapping[str, str] | None = None,
) -> AlreadyReviewed | None:
    """Decide whether the current head is already covered by a finished round.

    The guard fails toward review: any condition it cannot establish from the
    state means a round runs. In particular a prior that stores coverage but
    no run records, a trailing checkpoint record with no finished run before
    it, a partial or degraded last round, a changed patch hash, a never
    reviewed file, a model flag, a pending invalidation, or coverage the
    reuse gate does not inherit all run a round.

    Args:
        context: Collected review context for this run.
        prior: State loaded for this invocation, or ``None``.
        force_full: Whether ``--full`` was passed.
        reusable_rounds: Round numbers whose coverage this run may inherit
            (#2867); ``None`` inherits every record.
        env: Environment to read the force override from; the process
            environment when ``None``.

    Returns:
        The decision when the round can be skipped, else ``None``.
    """
    if force_full or force_requested(env=env) or prior is None:
        return None
    run = latest_finished_run(prior=prior)
    head = str(getattr(context, "head_ref", "") or "")
    if run is None or not head or run.identity.sha != head:
        return None
    if run.coverage.partial or latest_degradations(prior=prior):
        return None
    plan = plan_resume(
        context=context,
        prior=prior,
        force_full=False,
        reusable_rounds=reusable_rounds,
    )
    if not plan.eligible:
        return None
    if any(item.need not in _QUIET_NEEDS for item in plan.classified):
        return None
    return AlreadyReviewed(
        head_sha=head,
        round=run.identity.round,
        files_covered=len(plan.eligible),
        open_findings=sum(
            1 for record in prior.findings if record.status is FindingStatus.OPEN
        ),
    )


def _expects_inline_thread(*, record: FindingRecord, policy: PostingPolicy) -> bool:
    """Return whether the posting policy would have opened a thread for *record*.

    Mirrors :meth:`PostingPolicy.posts_inline` on the record's fields: a
    defect claim, or a question when questions are configured to post
    inline, in the inline severity tier at or above the confidence floor.
    Category routing (#2909) touches P3 only, so it cannot apply.

    Args:
        record: A tracked finding.
        policy: Posting policy the round was posted under.

    Returns:
        True when an inline thread was expected for the record.
    """
    if record.kind is FindingKind.QUESTION and not policy.post_questions_inline:
        return False
    if record.severity not in INLINE_SEVERITIES:
        return False
    confidence = coerce_confidence(raw=record.confidence)
    return confidence.numeric_order >= policy.inline_min_confidence.numeric_order


def publication_complete(
    *,
    prior: ReviewState,
    policy: PostingPolicy,
    find_sticky: Any,
) -> bool:
    """Return whether the last finished round's review reached the PR.

    Args:
        prior: State loaded for this invocation.
        policy: Posting policy the round was posted under.
        find_sticky: Callable taking ``marker=`` and returning the sticky
            comment, or ``None`` when absent or when the listing failed
            (:meth:`lintro.ai.integrations.github_pr.GitHubPRReporter.find_issue_comment`).

    Returns:
        True when the sticky comment exists and every open record the
        policy would have posted inline carries its thread id. Anything the
        listing cannot establish reads as not published.
    """
    if find_sticky(marker=STICKY_MARKER) is None:
        return False
    return all(
        record.inline_comment_id is not None
        for record in prior.findings
        if record.status is FindingStatus.OPEN
        and _expects_inline_thread(record=record, policy=policy)
    )
