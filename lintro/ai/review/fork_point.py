"""The merge base a pull-request round records (#2627, #3449).

Shared by the round's planning, which stores it for the next round's
merged-base check, and by the SIGTERM checkpoint, which proves a new
finding's base path over ``merge_base..head`` as the final round does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.delta import merge_base
from lintro.ai.review.enums.review_checkout import ReviewCheckout

if TYPE_CHECKING:
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = ["recorded_merge_base"]


def recorded_merge_base(*, context: ReviewContext, repo_root: str) -> str:
    """Return the merge base a pull-request round records, or ``""``.

    Only a ``--pr`` round with a checkout records one: without pull request
    metadata there is no base branch, and without a checkout the commits may
    not be local.

    Args:
        context: The round's context.
        repo_root: The repository to ask.

    Returns:
        ``merge-base(base, head)``, or an empty string.
    """
    if context.pr_metadata is None or context.checkout is ReviewCheckout.NONE:
        return ""
    return merge_base(
        repo_root=repo_root,
        base_sha=context.base_ref,
        head_sha=context.head_ref,
    )
