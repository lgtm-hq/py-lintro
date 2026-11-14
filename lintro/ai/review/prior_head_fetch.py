"""Make the commits a round maps through reachable in a shallow checkout (#3471).

The dogfood workflow checks the trusted base out at depth 1 and
:func:`~lintro.ai.review.context.git_ops.fetch_pr_head` adds the pull
request's head. The head an earlier round reviewed is usually not among
those commits once the branch was rebased, so git cannot map a prior
finding forward (:data:`~lintro.ai.review.models.delta_plan.LOCATION_UNMAPPED`).
The base tip is a shallow boundary too, so ``merge-base(base, head)`` is
unknown and every round looks as if its base moved.

Before a ``--pr`` round plans its scope, this module fetches, by SHA, the
base the pull request reports and the heads the prior state recorded: the
last finished round's head and the heads the open findings were last placed
at (:attr:`~lintro.ai.review.models.finding_record.FindingRecord.line_sha`).
It then deepens the base, a bounded number of commits, until
``merge-base(base, head)`` resolves. Nothing is fetched with ``--depth``: a
new shallow boundary could cut the head's own history, which the mapping
walks.

The fetches add only what the checkout does not hold. The head fetch
already brought the pull request's ancestry, so a previous head costs its
own commits and the base costs the commits it gained since the branch
point. Measured on py-lintro: under 0.7 s and 0.5 MB per commit fetched by
id, and 2.1 s and 0.5 MB for one ``--deepen=32``. Each git call keeps the
shared 120-second timeout. A head GitHub no longer serves (a force-push
followed by garbage collection) is logged and left out: the findings
recorded at it stay held where git cannot place them, as before.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.delta import merge_base
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.exceptions import ReviewContextError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "BASE_DEEPEN_STEPS",
    "MAX_PRIOR_HEADS",
    "PriorHeadFetch",
    "fetch_prior_heads",
    "prior_heads",
]

#: Distinct prior heads one round fetches; findings at any others stay held.
MAX_PRIOR_HEADS = 8

#: ``--deepen`` steps for the base side of a shallow checkout: at most three
#: fetches and 672 commits past the base before the merge-base is left unknown.
BASE_DEEPEN_STEPS = (32, 128, 512)

_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class PriorHeadFetch:
    """What a round fetched so its prior findings can be mapped.

    Attributes:
        fetched: Commits that were missing and are now present.
        unreachable: Commits that were missing and could not be fetched.
        merge_base: ``merge-base(base, head)`` after any deepening, or
            ``""`` when it is still unknown.
    """

    fetched: tuple[str, ...] = ()
    unreachable: tuple[str, ...] = ()
    merge_base: str = ""


def prior_heads(*, prior: ReviewState) -> tuple[str, ...]:
    """Return the heads the prior state maps its open findings from.

    Args:
        prior: State of the previous rounds.

    Returns:
        The last finished round's head first, then the heads open findings
        were placed at, most findings first, at most :data:`MAX_PRIOR_HEADS`.
        Anything that is not a full commit id (a hold such as ``"unmapped"``,
        an empty value) is left out.
    """
    ordered: list[str] = []
    finished = [run for run in prior.runs if not run.coverage.checkpoint]
    if finished:
        ordered.append(finished[-1].identity.sha)
    counts = Counter(
        record.line_sha
        for record in prior.findings
        if record.status is FindingStatus.OPEN and record.file
    )
    ordered.extend(sha for sha, _ in counts.most_common())
    unique = dict.fromkeys(sha for sha in ordered if _OID.fullmatch(sha))
    return tuple(unique)[:MAX_PRIOR_HEADS]


def fetch_prior_heads(
    *,
    prior: ReviewState | None,
    context: ReviewContext,
    repo_root: str,
) -> PriorHeadFetch:
    """Fetch the base and prior heads a ``--pr`` round needs, then deepen.

    Never raises: a failure is logged and the round runs with what the
    checkout holds, so every existing hold still applies.

    Args:
        prior: State of the previous rounds, or ``None`` on round one.
        context: The collected context: base, head, PR metadata, checkout.
        repo_root: The repository the round resolves commits in.

    Returns:
        What was fetched; empty on a run that is not a pull request or has
        no tree to ask git about.
    """
    head = context.head_ref
    if (
        context.pr_metadata is None
        or context.checkout is ReviewCheckout.NONE
        or not _OID.fullmatch(head)
    ):
        return PriorHeadFetch()
    base = context.base_ref if _OID.fullmatch(context.base_ref) else ""
    wanted = dict.fromkeys(
        [*([base] if base else []), *(prior_heads(prior=prior) if prior else ())],
    )
    missing = [sha for sha in wanted if not _has_commit(repo_root=repo_root, sha=sha)]
    fetched, unreachable = _fetch(repo_root=repo_root, shas=missing)
    for sha in unreachable:
        logger.warning(
            "Could not fetch {} recorded by an earlier review round; findings "
            "placed there stay held where git cannot map them.",
            sha[:12],
        )
    found = _deepen_to_merge_base(repo_root=repo_root, base=base, head=head)
    if fetched:
        logger.info(
            "Fetched {} commit(s) earlier review rounds recorded.",
            len(fetched),
        )
    return PriorHeadFetch(
        fetched=tuple(fetched),
        unreachable=tuple(unreachable),
        merge_base=found,
    )


def _has_commit(*, repo_root: str, sha: str) -> bool:
    """Tell whether the repository holds a commit.

    Args:
        repo_root: The repository.
        sha: A full commit id.

    Returns:
        True when git can read it as a commit.
    """
    try:
        result = _run_git(
            args=["-C", repo_root, "cat-file", "-e", f"{sha}^{{commit}}"],
            check=False,
        )
    except ReviewContextError:
        return False
    return result.returncode == 0


def _fetch(*, repo_root: str, shas: Sequence[str]) -> tuple[list[str], list[str]]:
    """Fetch commits by id: all at once, then one by one when that fails.

    A single unservable id fails the whole batch, so a failed batch is
    retried per commit. A timeout or a git that will not start stops the
    fetching: the remote is not answering.

    Args:
        repo_root: The repository.
        shas: The missing commits.

    Returns:
        ``(fetched, unreachable)``.
    """
    if not shas:
        return [], []
    ok = _fetch_batch(repo_root=repo_root, shas=shas)
    if ok is False and len(shas) > 1:
        for sha in shas:
            if _fetch_batch(repo_root=repo_root, shas=[sha]) is None:
                break
    fetched = [sha for sha in shas if _has_commit(repo_root=repo_root, sha=sha)]
    return fetched, [sha for sha in shas if sha not in fetched]


def _fetch_batch(*, repo_root: str, shas: Sequence[str]) -> bool | None:
    """Run one ``git fetch`` of commits by id.

    Args:
        repo_root: The repository.
        shas: The commits.

    Returns:
        True when git succeeded, False when it refused, and ``None`` when it
        timed out or could not start.
    """
    try:
        result = _run_git(
            args=["-C", repo_root, "fetch", "--no-tags", "origin", *shas],
            check=False,
        )
    except ReviewContextError as exc:
        logger.warning("Fetching earlier review heads stopped: {}", exc)
        return None
    if result.returncode != 0:
        logger.debug(
            "git fetch of {} failed: {}",
            ", ".join(sha[:12] for sha in shas),
            result.stderr.strip()[:200],
        )
        return False
    return True


def _is_shallow(*, repo_root: str) -> bool:
    """Tell whether the repository is a shallow clone.

    Args:
        repo_root: The repository.

    Returns:
        True when git reports a shallow repository.
    """
    try:
        result = _run_git(
            args=["-C", repo_root, "rev-parse", "--is-shallow-repository"],
            check=False,
        )
    except ReviewContextError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _deepen_to_merge_base(*, repo_root: str, base: str, head: str) -> str:
    """Deepen a shallow base, in bounded steps, until it meets the head.

    Args:
        repo_root: The repository.
        base: The base tip, or ``""`` when unknown.
        head: The head under review.

    Returns:
        ``merge-base(base, head)``, or ``""`` when it is still unknown.
    """
    if not base:
        return ""
    found = merge_base(repo_root=repo_root, base_sha=base, head_sha=head)
    for step in BASE_DEEPEN_STEPS:
        if found or not _is_shallow(repo_root=repo_root):
            break
        try:
            result = _run_git(
                args=[
                    "-C",
                    repo_root,
                    "fetch",
                    "--no-tags",
                    f"--deepen={step}",
                    "origin",
                    base,
                ],
                check=False,
            )
        except ReviewContextError as exc:
            logger.warning("Deepening the base stopped: {}", exc)
            break
        if result.returncode != 0:
            logger.warning(
                "Could not deepen the base to find its merge-base with the " "head: {}",
                result.stderr.strip()[:200],
            )
            break
        found = merge_base(repo_root=repo_root, base_sha=base, head_sha=head)
    return found
