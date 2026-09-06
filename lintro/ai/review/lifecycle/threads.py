"""Deciding which inline threads this round settles, and stamping them.

One pass over the threads a round touched: each finding the matcher settled is
stamped with the banner its stage earns, and — when ``review.auto_resolve``
allows and the stage is a clean fix — its thread is resolved. Rendering the
banner is :mod:`lintro.ai.review.lifecycle.banners`; this module owns the
decision and the API calls.

Every failure is reported rather than raised: a review that found real issues
must still post, even when GitHub refuses one edit. A failed stamp degrades to
"not stamped yet" and is retried next round, because the stored body still
differs from the rendered one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from loguru import logger

from lintro.ai.review.enums.lifecycle_stage import LifecycleStage
from lintro.ai.review.lifecycle.banners import (
    apply_lifecycle_block,
    render_lifecycle_block,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.lifecycle_sync_request import LifecycleSyncRequest
from lintro.ai.review.models.review_thread import ReviewThread

__all__ = [
    "LifecycleClient",
    "LifecycleReport",
    "sync_addressed_lifecycle",
]


class LifecycleClient(Protocol):
    """The GitHub operations the lifecycle needs, and nothing more.

    Structural typing keeps this module independent of the reporter class: the
    lifecycle only ever edits a comment and resolves a thread, so a caller can
    supply any object that does those two things.
    """

    def update_review_comment(self, *, comment_id: int, body: str) -> bool:
        """Edit an inline review comment in place.

        Args:
            comment_id: Comment to edit.
            body: New Markdown body.

        Returns:
            True when the edit succeeded.
        """
        ...  # pragma: no cover - structural type only

    def fetch_review_threads(self) -> dict[int, ReviewThread] | None:
        """Map each review thread's root comment id to the thread.

        Returns:
            The mapping, or ``None`` when it could not be fetched.
        """
        ...  # pragma: no cover - structural type only

    def resolve_review_thread(self, *, thread_id: str) -> bool:
        """Resolve a review thread.

        Args:
            thread_id: GraphQL node id of the thread.

        Returns:
            True when GitHub reports the thread as resolved.
        """
        ...  # pragma: no cover - structural type only


@dataclass(frozen=True, slots=True)
class LifecycleReport:
    """Outcome of one lifecycle synchronization pass.

    Attributes:
        edited: Keys of findings whose inline comment body was rewritten.
        unchanged: Keys whose comment already carried the exact stamp, so no
            request was made.
        resolved: Keys whose review thread was resolved this pass.
        failed: Keys whose comment edit or thread resolution did not take
            effect. The banner is retried on the next round because the stored
            body still differs from the rendered one — a failed stamp degrades
            to "not stamped yet", never to a crash.
    """

    edited: tuple[str, ...] = field(default_factory=tuple)
    unchanged: tuple[str, ...] = field(default_factory=tuple)
    resolved: tuple[str, ...] = field(default_factory=tuple)
    failed: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_failures(self) -> bool:
        """Return True when any stamp or resolution did not take effect."""
        return bool(self.failed)


@dataclass(frozen=True, slots=True)
class _LifecycleEdit:
    """One planned comment edit.

    Attributes:
        record: Finding record being stamped.
        stage: Lifecycle stage driving the stamp.
        historical: Whether the prompt panel is retitled.
        resolvable: Whether the thread may be resolved when config allows it.
        new_thread_url: URL of the regression's fresh thread, when known.
    """

    record: FindingRecord
    stage: LifecycleStage
    historical: bool
    resolvable: bool
    new_thread_url: str = ""


#: Which stage wins when one thread qualifies for two of them in a round.
#: Regression sorts first because it is the only stage that subsumes another:
#: its banner already carries the ``✔ Addressed in <sha>`` line for the round
#: that had fixed the finding, and it adds what a reader cannot get anywhere
#: else — this thread is stale, the live discussion moved to the new one
#: (mock-4 section 3, state D). "Addressed" outranks "partial" for the same
#: reason a whole is more than a part. Lower sorts first.
_STAGE_PRECEDENCE: dict[LifecycleStage, int] = {
    LifecycleStage.REGRESSED: 0,
    LifecycleStage.ADDRESSED: 1,
    LifecycleStage.PARTIAL: 2,
}


def _one_edit_per_thread(
    *,
    edits: Sequence[_LifecycleEdit],
) -> list[tuple[int, _LifecycleEdit]]:
    """Collapse the planned edits to at most one per inline comment.

    A single record can qualify for two stages in one round — a regression that
    also lost some of its occurrences. Editing its comment twice would apply the
    second banner to the pre-edit body, silently undoing the first edit's
    ``(historical)`` retitle and leaving a regression banner beside a live fix
    prompt.

    Args:
        edits: Planned edits, in stage order.

    Returns:
        ``(comment id, edit)`` pairs, one per comment, keeping the stage that
        most needs saying. Edits without a comment id are dropped: they have no
        thread to stamp.
    """
    chosen: dict[int, _LifecycleEdit] = {}
    for edit in edits:
        comment_id = edit.record.inline_comment_id
        if comment_id is None:
            continue
        held = chosen.get(comment_id)
        if held is None or (
            _STAGE_PRECEDENCE[edit.stage] < _STAGE_PRECEDENCE[held.stage]
        ):
            chosen[comment_id] = edit
    return list(chosen.items())


def sync_addressed_lifecycle(
    *,
    reporter: LifecycleClient,
    request: LifecycleSyncRequest,
) -> LifecycleReport:
    """Stamp — and optionally resolve — the threads this round settled.

    Args:
        reporter: GitHub reporter used to edit comments and resolve threads.
        request: The round's settled records and the context its banners read.

    Returns:
        What was edited, resolved, already current, or failed. Every failure is
        logged and reported rather than raised: a review that found real issues
        must still post, even when GitHub refuses one edit.
    """
    urls = request.new_thread_urls
    edits = [
        *(
            _LifecycleEdit(
                record=record,
                stage=LifecycleStage.ADDRESSED,
                historical=True,
                resolvable=True,
            )
            for record in request.resolved
        ),
        *(
            _LifecycleEdit(
                record=record,
                stage=LifecycleStage.PARTIAL,
                historical=False,
                resolvable=False,
            )
            for record in request.partial
        ),
        *(
            _LifecycleEdit(
                record=record,
                stage=LifecycleStage.REGRESSED,
                historical=True,
                resolvable=False,
                new_thread_url=urls.get(record.key, ""),
            )
            for record in request.regressed
        ),
    ]
    planned = _one_edit_per_thread(edits=edits)

    edited: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    resolvable_ids: list[tuple[str, int]] = []

    for comment_id, edit in planned:
        current = request.comment_bodies.get(comment_id)
        if current is None:
            logger.debug(
                "No stored body for comment {} — skipping the {} banner",
                comment_id,
                edit.stage.value,
            )
            continue
        body = apply_lifecycle_block(
            body=current,
            block=render_lifecycle_block(
                record=edit.record,
                stage=edit.stage,
                head_sha=request.head_sha,
                round_number=request.round_number,
                new_thread_url=edit.new_thread_url,
            ),
            historical=edit.historical,
        )
        if body == current:
            unchanged.append(edit.record.key)
        elif reporter.update_review_comment(comment_id=comment_id, body=body):
            edited.append(edit.record.key)
        else:
            logger.warning(
                "Could not stamp the {} banner onto review comment {}; it will "
                "be retried next round",
                edit.stage.value,
                comment_id,
            )
            failed.append(edit.record.key)
            continue
        if edit.resolvable and request.auto_resolve:
            resolvable_ids.append((edit.record.key, comment_id))

    resolved_keys, resolve_failures = _resolve_threads(
        reporter=reporter,
        targets=resolvable_ids,
    )
    return LifecycleReport(
        edited=tuple(edited),
        unchanged=tuple(unchanged),
        resolved=tuple(resolved_keys),
        failed=(*failed, *resolve_failures),
    )


def _resolve_threads(
    *,
    reporter: LifecycleClient,
    targets: Sequence[tuple[str, int]],
) -> tuple[list[str], list[str]]:
    """Resolve the review threads rooted at the given comments.

    Args:
        reporter: GitHub reporter used for the GraphQL calls.
        targets: ``(finding key, root comment id)`` pairs to resolve.

    Returns:
        Tuple of ``(resolved keys, failed keys)``. A thread GitHub already
        reports as resolved counts as resolved without a second mutation.
    """
    if not targets:
        return [], []
    threads: dict[int, ReviewThread] | None = reporter.fetch_review_threads()
    if threads is None:
        logger.warning(
            "Could not list review threads — {} thread(s) keep their banner but "
            "stay unresolved",
            len(targets),
        )
        return [], [key for key, _ in targets]

    resolved: list[str] = []
    failed: list[str] = []
    for key, comment_id in targets:
        thread = threads.get(comment_id)
        if thread is None:
            logger.debug("No review thread found for comment {}", comment_id)
            failed.append(key)
            continue
        if thread.is_resolved:
            resolved.append(key)
            continue
        if reporter.resolve_review_thread(thread_id=thread.node_id):
            resolved.append(key)
        else:
            logger.warning("Could not resolve review thread for comment {}", comment_id)
            failed.append(key)
    return resolved, failed
