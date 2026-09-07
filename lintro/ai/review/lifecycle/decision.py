"""The one place create-vs-update-vs-supersede is decided (#2305, epic #1974).

Every comment a review writes on a pull request — the mission-control board,
the history archive, and the failure surface — is either posted for the first
time, edited in place, or replaced because GitHub refuses the edit. Before
this module each posting path answered that question inline, which is how the
success path and the error path came to disagree about what a leftover comment
from another actor meant.

:func:`decide` is pure: it reads what is already on the pull request and
answers with an action. Performing the action is
:mod:`lintro.ai.review.lifecycle.comments`, which calls back into ``decide``
once GitHub has said whether the edit was allowed, so the fallback is the
same decision rather than a second one.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.comment_action import CommentAction
from lintro.ai.review.enums.comment_kind import CommentKind

__all__ = ["CommentPlan", "ExistingComment", "decide"]


@dataclass(frozen=True, kw_only=True, slots=True)
class ExistingComment:
    """What is already on the pull request for one comment kind.

    Attributes:
        comment_id: Id of the live comment, or ``None`` when the kind has
            never been posted (or the comment has since been deleted).
        editable: Whether this actor may edit that comment. It starts
            optimistic — the only way to find out is to try — and is set
            false once GitHub answers ``403``.
    """

    comment_id: int | None = None
    editable: bool = True


@dataclass(frozen=True, kw_only=True, slots=True)
class CommentPlan:
    """The decided action for one comment kind.

    Attributes:
        kind: The comment the plan is about.
        action: What to do with it.
        comment_id: The comment the action operates on: the one to edit, or
            the one to delete after the replacement lands. ``None`` for a
            create.
        body: The Markdown to write.
    """

    kind: CommentKind
    action: CommentAction
    comment_id: int | None
    body: str


def decide(
    *,
    kind: CommentKind,
    existing: ExistingComment,
    new: str,
) -> CommentPlan:
    """Decide what to do with a review comment of one kind.

    The rule is the same for every kind, and that is the point: a board, an
    archive and a failure surface are all one-per-pull-request comments
    identified by a marker, so nothing about *which* comment it is should
    change whether it is created, edited, or replaced.

    Args:
        kind: Which of the review's comments this is.
        existing: What is already on the pull request for that kind.
        new: The Markdown body to end up with.

    Returns:
        CommentPlan: The action, the comment it applies to, and the body.
    """
    if existing.comment_id is None:
        return CommentPlan(
            kind=kind,
            action=CommentAction.CREATE,
            comment_id=None,
            body=new,
        )
    if not existing.editable:
        return CommentPlan(
            kind=kind,
            action=CommentAction.SUPERSEDE,
            comment_id=existing.comment_id,
            body=new,
        )
    return CommentPlan(
        kind=kind,
        action=CommentAction.UPDATE,
        comment_id=existing.comment_id,
        body=new,
    )
