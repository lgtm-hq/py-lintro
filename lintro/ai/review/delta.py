"""Delta rounds: read what changed since the last posted round (#2627).

Every round of a ``--pr`` review used to embed the whole pull-request diff in
its chunk calls, so round five of a large PR re-read files that had not
changed since round one. Coverage resume (ADR-0007) already keeps the
provider off files whose whole-PR patch hash is unchanged; this module
narrows what the remaining calls *read*:

- :func:`plan_delta` decides the round's scope from the prior round's head:
  round one, an unknown head, a rewritten branch (the prior head is not an
  ancestor of this one), a run without a tree, or ``--full`` all read the
  whole diff, each with its own :class:`DeltaReason` for the sticky comment.
- :func:`delta_hunks` computes ``since..head`` per file in the PR head
  worktree #2744 provides, restricted to files the pull request itself
  changes — a file that only a merge from ``main`` brought in is not the
  PR's change and is never embedded.
- :func:`apply_delta_hunks` swaps each queued file's embedded text for its
  delta hunk. A queued file with no delta hunk (re-queued by an open thread
  or an invalidation, not by a change) keeps its whole-PR hunk: the reviewer
  needs the code the thread is about.
- :func:`open_thread_paths` names the files with an open finding so the
  resume queue re-reads them every delta round.

What the round *reports* is not narrowed: a causal finding on a file the
agent read in the worktree is posted exactly as on a full round. The
coverage identity of a file — its whole-PR patch hash — is never replaced by
a delta hash, so ADR-0007's rule that coverage below 100% at HEAD forces
``INCOMPLETE`` holds on a delta round unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.delta_plan import DeltaPlan

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "apply_delta_hunks",
    "delta_hunks",
    "open_thread_paths",
    "plan_delta",
]


def plan_delta(
    *,
    prior: ReviewState | None,
    head_sha: str,
    repo_root: str,
    checkout: ReviewCheckout,
    force_full: bool,
) -> DeltaPlan:
    """Decide whether this round reads the whole diff or the delta.

    Args:
        prior: State of the previous rounds, or ``None`` on round one.
        head_sha: The head commit under review.
        repo_root: The repository the range is resolved in (the PR head
            worktree on a ``--pr`` run).
        checkout: What tree the run has; :attr:`ReviewCheckout.NONE` has no
            repository to resolve a range in.
        force_full: ``--full``.

    Returns:
        The plan; :attr:`DeltaPlan.is_delta` is True only when the prior head
        is a known ancestor of ``head_sha``.
    """
    if force_full:
        return DeltaPlan.full(reason=DeltaReason.EXPLICIT_FULL)
    if prior is None or not prior.runs:
        return DeltaPlan.full(reason=DeltaReason.FIRST_ROUND)
    since = prior.runs[-1].identity.sha
    if not since:
        return DeltaPlan.full(reason=DeltaReason.NO_PRIOR_HEAD)
    if checkout is ReviewCheckout.NONE:
        return DeltaPlan.full(reason=DeltaReason.NO_TREE)
    if since == head_sha:
        # Nothing moved: a re-run of the same head is a full read; resume
        # keeps the provider off what is already covered.
        return DeltaPlan.full(reason=DeltaReason.NOT_ANCESTOR)
    if not _is_ancestor(repo_root=repo_root, ancestor=since, descendant=head_sha):
        logger.info(
            "Prior round head {} is not an ancestor of {}; reading the whole diff.",
            since[:12],
            head_sha[:12],
        )
        return DeltaPlan.full(reason=DeltaReason.NOT_ANCESTOR)
    return DeltaPlan(reason=DeltaReason.DELTA, since_sha=since)


def _is_ancestor(*, repo_root: str, ancestor: str, descendant: str) -> bool:
    """Tell whether ``ancestor`` reaches ``descendant`` in ``repo_root``.

    A commit the repository does not hold (the old head of a force-pushed
    branch, which the PR ref no longer reaches) is not an ancestor.

    Args:
        repo_root: The repository to ask.
        ancestor: The candidate ancestor commit.
        descendant: The commit it should reach.

    Returns:
        True only when git confirms the relation.
    """
    try:
        result = _run_git(
            args=["-C", repo_root, "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("Ancestor probe failed: {}", exc)
        return False
    return result.returncode == 0


def delta_hunks(
    *,
    repo_root: str,
    since_sha: str,
    head_sha: str,
    pr_paths: Iterable[str],
) -> dict[str, str]:
    """Return the ``since..head`` diff per file, for the PR's own files only.

    Args:
        repo_root: The repository holding both commits.
        since_sha: The prior round's head.
        head_sha: This round's head.
        pr_paths: The files the pull request changes as a whole; a file
            outside this set (brought in by a merge from ``main``) is dropped.

    Returns:
        ``{path: unified diff}`` for the PR files that changed in the range;
        empty when the range cannot be computed, which callers treat as "no
        delta text" (the whole-PR hunks stay).
    """
    allowed = set(pr_paths)
    if not allowed:
        return {}
    try:
        result = _run_git(
            args=[
                "-C",
                repo_root,
                "diff",
                "--no-color",
                "--no-ext-diff",
                f"{since_sha}..{head_sha}",
                "--",
                *sorted(allowed),
            ],
            check=False,
        )
    except ReviewContextError as exc:
        logger.warning(
            "Could not compute the delta diff ({}); reading the whole diff.",
            exc,
        )
        return {}
    if result.returncode != 0:
        logger.warning(
            "git diff {}..{} failed ({}); reading the whole diff.",
            since_sha[:12],
            head_sha[:12],
            result.stderr.strip()[:200],
        )
        return {}
    return {
        path: hunk
        for path, hunk in split_unified_diff_by_file(unified_diff=result.stdout).items()
        if path in allowed
    }


def apply_delta_hunks(
    *,
    chunks: list[ReviewChunk],
    hunks: Mapping[str, str],
) -> list[ReviewChunk]:
    """Embed each chunk file's delta hunk in place of its whole-PR hunk.

    Args:
        chunks: The round's chunks after resume filtering.
        hunks: Output of :func:`delta_hunks`.

    Returns:
        The chunks with their ``diff`` rebuilt; a chunk none of whose files
        has a delta hunk is returned as is.
    """
    if not hunks:
        return chunks
    rebuilt: list[ReviewChunk] = []
    for chunk in chunks:
        per_file = split_unified_diff_by_file(unified_diff=chunk.diff)
        if not any(path in hunks for path in per_file):
            rebuilt.append(chunk)
            continue
        # Keep the chunk's own file order; a file with no delta hunk keeps
        # its whole-PR text.
        parts = [hunks.get(path, text) for path, text in per_file.items()]
        rebuilt.append(replace(chunk, diff="".join(parts)))
    return rebuilt


def open_thread_paths(*, prior: ReviewState | None) -> tuple[str, ...]:
    """Name the files that carry an open finding from a prior round.

    Args:
        prior: State of the previous rounds, or ``None``.

    Returns:
        Sorted unique paths; empty without prior state.
    """
    if prior is None:
        return ()
    return tuple(
        sorted(
            {
                record.file
                for record in prior.findings
                if record.status is FindingStatus.OPEN and record.file
            },
        ),
    )
