"""A working tree at the pull request's head for the agent to read (#2733).

On ``--pr`` the diff and refs come from ``gh`` and the CLI transport's agent
(the question pass, every chunk call, the synthesis and verification passes)
used to explore whatever tree the command ran in: today's ``main`` for a
corpus replay of a merged PR, so questions and findings could cite code the
PR never saw (#2732). This module pins that tree to the PR head: it fetches
``refs/pull/<n>/head`` into a private ref and checks the head commit out in
a temporary worktree under the cache directory, which becomes ``repo_root``
for every provider call of the run and is removed when the run ends,
whichever way it ends. When no repository is available the run is told so
and every call goes out without tools (:attr:`ReviewCheckout.NONE`), never
against the ambient tree.
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.enums import AITransport
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    CoverageDegradation,
)

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.review.run_planning import ReviewRunPlan

__all__ = [
    "PrHeadWorktree",
    "checkout_pr_head",
    "no_tree_degradations",
    "prune_stale_pr_worktrees",
    "remove_pr_head",
]

#: Where the head worktrees live, relative to the repository root.
_WORKTREE_DIR = Path(".lintro-cache") / "ai" / "pr-heads"


@dataclass(frozen=True, slots=True)
class PrHeadWorktree:
    """A checked-out PR head the run may read.

    Attributes:
        path: Absolute path of the worktree; the run's ``repo_root``.
        repo_root: The repository the worktree belongs to (for removal).
        head_oid: The commit checked out, verified against the PR's head.
    """

    path: str
    repo_root: str
    head_oid: str


def checkout_pr_head(*, pr_number: int, head_oid: str) -> PrHeadWorktree | None:
    """Fetch the PR head and check it out in a temporary worktree.

    Args:
        pr_number: The pull request number, for ``refs/pull/<n>/head``.
        head_oid: The head commit ``gh`` reported; the fetched ref must
            resolve to it, or the worktree is not created.

    Returns:
        The worktree, or ``None`` when the command does not run inside a
        git repository, the fetch is refused, or the fetched head is not
        the one ``gh`` reported. ``None`` means "no tree": the caller runs
        without tools rather than against the ambient tree.
    """
    try:
        root = _run_git(args=["rev-parse", "--show-toplevel"], check=False)
    except ReviewContextError:
        return None
    if root.returncode != 0 or not root.stdout.strip():
        return None
    repo_root = root.stdout.strip()
    prune_stale_pr_worktrees(repo_root=repo_root)
    ref = f"refs/lintro/pr/{pr_number}"
    try:
        fetched = _run_git(
            args=["fetch", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{ref}"],
            check=False,
        )
        if fetched.returncode != 0:
            logger.warning(
                "Could not fetch refs/pull/{}/head; the review runs without a tree.",
                pr_number,
            )
            return None
        resolved = _run_git(args=["rev-parse", ref], check=False)
        if resolved.returncode != 0 or resolved.stdout.strip() != head_oid:
            logger.warning(
                "refs/pull/{}/head resolved to {} but gh reported {}; the review "
                "runs without a tree.",
                pr_number,
                resolved.stdout.strip()[:12],
                head_oid[:12],
            )
            return None
        path = Path(repo_root) / _WORKTREE_DIR / f"{pr_number}-{head_oid[:12]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        added = _run_git(
            args=["worktree", "add", "--detach", str(path), head_oid],
            check=False,
        )
        if added.returncode != 0:
            logger.warning(
                "Could not check out the PR head in a worktree ({}); the review "
                "runs without a tree.",
                added.stderr.strip()[:200],
            )
            return None
    except ReviewContextError as exc:
        logger.warning(
            "PR head checkout failed ({}); the review runs without a tree.",
            exc,
        )
        return None
    logger.info(
        "Reviewing PR #{} against its head {} at {}",
        pr_number,
        head_oid[:12],
        path,
    )
    return PrHeadWorktree(path=str(path), repo_root=repo_root, head_oid=head_oid)


def remove_pr_head(worktree: PrHeadWorktree | None) -> None:
    """Remove a head worktree; a no-op for ``None``.

    Safe to call more than once and on every exit path: a worktree already
    gone is not an error, and a failure to remove is logged, not raised,
    since the review's result is already in hand.

    Args:
        worktree: The worktree to remove.
    """
    if worktree is None:
        return
    try:
        _run_git(
            args=[
                "-C",
                worktree.repo_root,
                "worktree",
                "remove",
                "--force",
                worktree.path,
            ],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("git worktree remove failed: {}", exc)
    shutil.rmtree(worktree.path, ignore_errors=True)


def prune_stale_pr_worktrees(*, repo_root: str) -> None:
    """Drop head worktrees a killed run left behind.

    A SIGKILL cannot run the cleanup, so every run starts by pruning what
    an earlier one could not remove: git's own record first, then any
    directory still under the cache path.

    Args:
        repo_root: The repository whose cache directory to sweep.
    """
    base = Path(repo_root) / _WORKTREE_DIR
    if not base.is_dir():
        return
    with contextlib.suppress(ReviewContextError):
        _run_git(args=["-C", repo_root, "worktree", "prune"], check=False)
    for stale in base.iterdir():
        if stale.is_dir():
            remove_pr_head(
                PrHeadWorktree(path=str(stale), repo_root=repo_root, head_oid=""),
            )


def no_tree_degradations(
    *,
    plan: ReviewRunPlan,
    ai_config: AIConfig,
) -> tuple[CoverageDegradation, ...]:
    """Return the run-level row recording that the agent had no tree.

    Args:
        plan: The resolved run plan (``tools_disabled``).
        ai_config: Effective AI configuration; only the CLI transport has an
            agent that could have read a tree.

    Returns:
        One ``NO_TREE_FOR_AGENT`` narrative row when tools were off for the
        run on the CLI transport, else nothing.
    """
    if not plan.tools_disabled or ai_config.transport is not AITransport.CLI:
        return ()
    return (
        CoverageDegradation(
            reason=CoverageDegradationReason.NO_TREE_FOR_AGENT,
            chunk_index=CARRIED_CHUNK_INDEX,
            split=False,
        ),
    )
