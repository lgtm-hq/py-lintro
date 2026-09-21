"""Keep a PR head tree from outliving a failed preparation (#2733).

The tree :mod:`lintro.ai.review.pr_head` checks out exists from context
collection on, and the run's own ``finally`` takes over only once the run is
entered. :class:`RemoveOnError` guards the steps in between.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.pr_head import remove_pr_head

if TYPE_CHECKING:
    from lintro.ai.review.pr_head import PrHeadWorktree


class RemoveOnError:
    """Remove a tree if the guarded block exits by any exception.

    The tree exists from context collection on, and every step between
    that and the run's own ``finally`` — filters, validation, checklist
    selection, the lint digest, custom-agent resolution — can raise. Any
    exception, an interrupt included, must not leave the tree to the next
    sweep. A normal exit keeps it: the run is about to use it.

    Attributes:
        worktree: The tree to remove on error; ``None`` guards nothing.
    """

    worktree: PrHeadWorktree | None

    def __init__(self, worktree: PrHeadWorktree | None) -> None:
        """Guard one tree.

        Args:
            worktree: The tree to remove on error; ``None`` guards nothing.
        """
        self.worktree = worktree

    def __enter__(self) -> RemoveOnError:
        """Enter the guarded block.

        Returns:
            The guard itself.
        """
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Remove the tree when the block raised; the exception propagates.

        Args:
            exc_type: The exception type, or ``None`` on a clean exit.
            exc: The exception instance.
            tb: The traceback.
        """
        if exc_type is not None:
            remove_pr_head(self.worktree)
