"""Delta rounds: read what changed since the last posted round (#2627).

A delta round narrows what the model reads and what it may resolve; it never
narrows what it may report.

Every round of a ``--pr`` review used to embed the whole pull-request diff in
its chunk calls, so round five of a large PR re-read files that had not
changed since round one. Coverage resume (ADR-0007) already keeps the
provider off files whose whole-PR patch hash is unchanged; this module
narrows what the remaining calls *read*:

- :func:`plan_delta` decides the round's scope from the prior round's head.
  Round one, a non-PR run, an unknown head, the same head again, a rewritten
  branch (the prior head is not an ancestor of this one), a base that was
  merged in since the prior round, a run without a tree, or ``--full`` all
  read the whole diff, each with its own :class:`DeltaReason` for the sticky.
- :func:`delta_hunks` computes ``since..head`` per file in the PR head
  worktree #2744 provides, restricted to files the pull request itself
  changes.
- :func:`apply_delta_hunks` gives each queued file's chunk a ``read_diff``
  — the text the prompt embeds — while ``ReviewChunk.diff`` keeps the
  whole-PR hunk the diff gate, the cross-chunk guard and the budgets see. A
  delta hunk that is not smaller than the whole hunk (a large change then a
  large revert) is not used; a file re-queued by an open thread with no
  change since keeps its whole hunk: the reviewer needs the code the thread
  is about. The application also returns the old-side line ranges the delta
  showed — the prior head's coordinates, which is what a prior finding's
  line is in — so the matcher carries, never resolves, a prior finding on a
  line the round did not re-read.
- :func:`open_thread_paths` names the files with an open finding so the
  resume queue re-reads them every delta round.

The coverage identity of a file — its whole-PR patch hash — is never
replaced by a delta hash, so ADR-0007's rule that coverage below 100% at
HEAD forces ``INCOMPLETE`` holds on a delta round unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.prompts.review import REVIEW_DELTA_SCOPE_NOTE
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
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_state import ReviewState

#: The range recorded for a narrowed file whose delta showed no prior line:
#: no line is negative, so nothing on the file can match it and resolve.
_NO_LINE = (-1, -1)

__all__ = [
    "DeltaApplication",
    "apply_delta_hunks",
    "delta_hunks",
    "delta_scope_note",
    "merge_base",
    "open_thread_paths",
    "plan_delta",
]


def plan_delta(
    *,
    prior: ReviewState | None,
    context: ReviewContext,
    repo_root: str,
    force_full: bool,
) -> DeltaPlan:
    """Decide whether this round reads the whole diff or the delta.

    Args:
        prior: State of the previous rounds, or ``None`` on round one.
        context: The collected context: head, base, PR metadata, checkout.
        repo_root: The repository the range is resolved in (the PR head
            worktree on a ``--pr`` run).
        force_full: ``--full``.

    Returns:
        The plan; :attr:`DeltaPlan.is_delta` is True only when the prior head
        is a known ancestor of the head and the base was not merged in since.
    """
    head_sha = context.head_ref
    if force_full:
        return DeltaPlan.full(reason=DeltaReason.EXPLICIT_FULL)
    if context.pr_metadata is None:
        return DeltaPlan.full(reason=DeltaReason.NOT_PR)
    if prior is None or not prior.runs:
        return DeltaPlan.full(reason=DeltaReason.FIRST_ROUND)
    last = prior.runs[-1].identity
    since = last.sha
    if not since:
        return DeltaPlan.full(reason=DeltaReason.NO_PRIOR_HEAD)
    if context.checkout is ReviewCheckout.NONE:
        return DeltaPlan.full(reason=DeltaReason.NO_TREE)
    if since == head_sha:
        return DeltaPlan.full(reason=DeltaReason.SAME_HEAD)
    if not _is_ancestor(repo_root=repo_root, ancestor=since, descendant=head_sha):
        logger.info(
            "Prior round head {} is not an ancestor of {}; reading the whole diff.",
            since[:12],
            head_sha[:12],
        )
        return DeltaPlan.full(reason=DeltaReason.NOT_ANCESTOR)
    if _base_moved(
        repo_root=repo_root,
        base_sha=context.base_ref,
        since=since,
        head_sha=head_sha,
        prior_merge_base=last.merge_base,
    ):
        return DeltaPlan.full(reason=DeltaReason.BASE_MOVED)
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


def merge_base(*, repo_root: str, base_sha: str, head_sha: str) -> str:
    """Return ``merge-base(base, head)``, or ``""`` when git cannot say.

    Recorded on every round so the next one can tell whether the base was
    merged into the branch in between.

    Args:
        repo_root: The repository to ask.
        base_sha: The base branch tip ``gh`` reported.
        head_sha: The head under review.

    Returns:
        The merge-base commit, or an empty string.
    """
    if not base_sha or not head_sha:
        return ""
    try:
        result = _run_git(
            args=["-C", repo_root, "merge-base", base_sha, head_sha],
            check=False,
        )
    except ReviewContextError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _base_moved(
    *,
    repo_root: str,
    base_sha: str,
    since: str,
    head_sha: str,
    prior_merge_base: str,
) -> bool:
    """Tell whether the base branch entered ``since..head``.

    A merge from the base brings its commits into the two-dot range, and
    for a file both sides touch the delta hunk would carry the base's lines
    as if the PR had written them. The recorded merge-base moving is the
    direct signal; a prior record without one (written before this field)
    falls back to looking for merge commits in the range.

    Args:
        repo_root: The repository to ask.
        base_sha: The base branch tip.
        since: The prior round's head.
        head_sha: This round's head.
        prior_merge_base: The merge-base the prior round recorded, or ``""``.

    Returns:
        True when the delta could carry base-authored lines.
    """
    if prior_merge_base:
        current = merge_base(repo_root=repo_root, base_sha=base_sha, head_sha=head_sha)
        return current != prior_merge_base
    try:
        result = _run_git(
            args=["-C", repo_root, "rev-list", "--merges", f"{since}..{head_sha}"],
            check=False,
        )
    except ReviewContextError:
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def delta_hunks(
    *,
    repo_root: str,
    since_sha: str,
    head_sha: str,
    pr_paths: Iterable[str],
) -> dict[str, str] | None:
    """Return the ``since..head`` diff per file, for the PR's own files only.

    Args:
        repo_root: The repository holding both commits.
        since_sha: The prior round's head.
        head_sha: This round's head.
        pr_paths: The files the pull request changes as a whole; a file
            outside this set (brought in by a merge from ``main``) is dropped.

    Returns:
        ``{path: unified diff}`` for the PR files that changed in the range,
        or ``None`` when git could not compute the range — the caller then
        records a full read rather than a delta it never got.
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
        logger.warning("Could not compute the delta diff ({}).", exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "git diff {}..{} failed ({}).",
            since_sha[:12],
            head_sha[:12],
            result.stderr.strip()[:200],
        )
        return None
    return {
        path: hunk
        for path, hunk in split_unified_diff_by_file(unified_diff=result.stdout).items()
        if path in allowed
    }


@dataclass(frozen=True, slots=True)
class DeltaApplication:
    """What :func:`apply_delta_hunks` did to a round's chunks.

    Attributes:
        chunks: The chunks, each queued file carrying its ``read_diff``
            where a smaller delta hunk existed.
        reviewed_ranges: ``(path, start, end)`` OLD-side line ranges (the
            prior head's coordinates) the round's delta hunks show for the
            files whose text was narrowed; a prior finding on such a file
            outside these ranges is carried, not resolved. Files read whole
            are absent: every line counts.
        larger: Files whose delta hunk was not smaller than the whole hunk
            and were read whole.
    """

    chunks: list[ReviewChunk]
    reviewed_ranges: tuple[tuple[str, int, int], ...] = ()
    larger: tuple[str, ...] = ()


def _old_side_ranges(delta: str) -> list[tuple[int, int]]:
    """Return the OLD-side line ranges of a delta hunk's ``@@`` headers.

    A prior round's finding carries a line in the prior head's coordinates,
    and the old side of ``since..head`` is exactly that coordinate system:
    a prior line was re-read iff the delta showed it, context included. The
    new side would compare a prior line against current-head numbers, which
    an insertion or deletion above the finding shifts (#2627).

    Args:
        delta: One file's ``since..head`` unified diff.

    Returns:
        ``(start, end)`` pairs, inclusive, in hunk order; a pure insertion
        (old length 0) shows no old line and contributes nothing.
    """
    ranges: list[tuple[int, int]] = []
    for line in delta.splitlines():
        if not line.startswith("@@ "):
            continue
        parts = line.split()
        if len(parts) < 3 or not parts[1].startswith("-"):
            continue
        start_text, _, length_text = parts[1][1:].partition(",")
        if not start_text.isdigit() or (length_text and not length_text.isdigit()):
            continue
        start = int(start_text)
        length = int(length_text) if length_text else 1
        if length > 0:
            ranges.append((start, start + length - 1))
    return ranges


def apply_delta_hunks(
    *,
    chunks: list[ReviewChunk],
    hunks: Mapping[str, str],
    since_sha: str = "",
) -> DeltaApplication:
    """Give each chunk file a ``read_diff`` where its delta hunk is smaller.

    Args:
        chunks: The round's chunks after resume filtering.
        hunks: Output of :func:`delta_hunks`.
        since_sha: The prior round's head, stamped on narrowed chunks so the
            prompt can say what range the text is.

    Returns:
        The application; chunks none of whose files narrowed are unchanged.
    """
    if not hunks:
        return DeltaApplication(chunks=chunks)
    rebuilt: list[ReviewChunk] = []
    ranges: list[tuple[str, int, int]] = []
    larger: list[str] = []
    for chunk in chunks:
        per_file = split_unified_diff_by_file(unified_diff=chunk.diff)
        parts: list[str] = []
        narrowed = False
        for path, whole in per_file.items():
            delta = hunks.get(path)
            if delta is None:
                parts.append(whole)
                continue
            if len(delta) >= len(whole):
                larger.append(path)
                parts.append(whole)
                continue
            narrowed = True
            parts.append(delta)
            shown = _old_side_ranges(delta)
            # A delta of pure insertions shows no prior line: the file is still
            # narrowed, and the sentinel keeps it in the mapping so nothing on
            # it resolves, rather than reading as "read whole".
            if shown:
                ranges.extend((path, start, end) for start, end in shown)
            else:
                ranges.append((path, *_NO_LINE))
        rebuilt.append(
            (
                replace(chunk, read_diff="".join(parts), read_since=since_sha)
                if narrowed
                else chunk
            ),
        )
    return DeltaApplication(
        chunks=rebuilt,
        reviewed_ranges=tuple(ranges),
        larger=tuple(larger),
    )


def delta_scope_note(*, chunk: ReviewChunk) -> str:
    """The prompt line saying a chunk's text is a delta, or nothing.

    Args:
        chunk: The chunk being prompted.

    Returns:
        The rendered note on a delta round; ``""`` on a full round.
    """
    if chunk.read_diff is None:
        return ""
    return (
        REVIEW_DELTA_SCOPE_NOTE.format(since=chunk.read_since[:12], head="HEAD") + "\n"
    )


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
