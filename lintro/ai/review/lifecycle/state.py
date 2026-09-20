"""Where a round's review state is read from and written back to (#2305).

The lifecycle owns more than the comments: it owns the state those comments
render. Loading the prior round's state and persisting the advanced one used
to sit in the CLI command module beside the flag parsing, which put the
decision "is this a first round or a continuation?" one import away from the
decision "is this comment created or updated?" — two halves of the same
question, answered in two places.

Both halves live here now. Reading prefers the authoritative store for the
environment: workflow artifacts under CI, the local ledger otherwise. Neither
crosses into the other, which is the #2154 trust boundary.

When neither store has anything and the run is going to post, the sticky
comment's own leftover blob is the last resort — the same fallback
:func:`~lintro.ai.review.github.post_review_to_github` applies before it
renders. It is read *here*, once, so the board and the state written beside it
describe the same history: a round that rendered three recovered runs and then
persisted a fresh round 1 would lose them for good, because the next round
prefers the store it just wrote. A pre-v2 blob is not migrated (#2305) — it is
treated as absent and the round starts a fresh history.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace

from loguru import logger

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.state_store import (
    load_ci_state,
    load_local_state,
    local_ledger_key,
    state_dir,
    write_local_state,
    write_state_part,
)
from lintro.ai.review.sticky import advance_review_state

__all__ = [
    "departed_paths",
    "load_prior_review_state",
    "persist_review_state",
    "resolve_prior_state",
]

#: Workflow filename recorded on every persisted state part.
_WORKFLOW = "ai-review.yml"


def _in_actions() -> bool:
    """Return True when the process is running inside GitHub Actions."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def load_prior_review_state(
    *,
    pr_number: int | None,
    head_ref: str,
    repo: str,
    post: bool = False,
) -> ReviewState:
    """Load the state this round continues from.

    Args:
        pr_number: Pull request number, or ``None`` for a local branch run.
        head_ref: Head ref reviewed in this round, part of the ledger key.
        repo: ``owner/name`` slug the state must belong to.
        post: Whether this run will comment on the pull request. Only then is
            the sticky comment consulted, so a run that writes nothing also
            reads nothing over the network.

    Returns:
        ReviewState: The prior state, or an empty state for a first round.
    """
    stored = _load_stored_state(
        pr_number=pr_number,
        head_ref=head_ref,
        repo=repo,
    )
    if not post:
        return stored
    return resolve_prior_state(
        prior_state=stored,
        sticky_state=_sticky_state(pr_number=pr_number, repo=repo),
    )


def _load_stored_state(
    *,
    pr_number: int | None,
    head_ref: str,
    repo: str,
) -> ReviewState:
    """Read the authoritative store for this environment.

    Args:
        pr_number: Pull request number, or ``None`` for a local branch run.
        head_ref: Head ref reviewed in this round, part of the ledger key.
        repo: ``owner/name`` slug the state must belong to.

    Returns:
        ReviewState: Workflow-artifact state under CI, the local ledger
        otherwise, or an empty state when neither holds this pull request.
        CI never reads the ledger and a local run never reads the artifacts:
        the #2154 trust boundary.
    """
    if _in_actions():
        return load_ci_state(
            directory=state_dir(ci=True),
            repo=repo,
            pr_number=pr_number or 0,
        )
    local = load_local_state(
        key=local_ledger_key(pr_number=pr_number, head_ref=head_ref),
        repo=repo,
        pr_number=pr_number,
    )
    if local.coverage or local.runs or local.findings:
        return local
    return ReviewState()


def _sticky_state(*, pr_number: int | None, repo: str) -> ReviewState:
    """Recover whatever state the sticky comment still carries.

    Nothing has *written* this blob since #2154 moved authoritative state to
    workflow artifacts, so it only ever answers for a comment an older lintro
    left behind. Reaching GitHub can fail for reasons that are not this
    review's problem — no context, no token, an unreachable API — and those
    are an empty state: a review must never fail because a comment could not
    be read.

    Only the transport-and-parse families are caught. ``urllib`` raises
    ``URLError`` (an ``OSError``) and the JSON decoder raises a ``ValueError``;
    anything else escaping here is a defect in this package, and swallowing it
    would turn a bug into a pull request whose history quietly restarts.

    Args:
        pr_number: Pull request number, or ``None`` when it is unknown.
        repo: ``owner/name`` slug the comment belongs to.

    Returns:
        ReviewState: The decoded state, or an empty state.
    """
    from lintro.ai.integrations.github_pr import GitHubPRReporter
    from lintro.ai.review.lifecycle.comments import load_sticky_comment

    try:
        _existing, state = load_sticky_comment(
            reporter=GitHubPRReporter(pr_number=pr_number, repo=repo or None),
        )
    except (OSError, ValueError) as exc:
        logger.debug(
            "Could not read the sticky comment for prior state ({}); this "
            "round starts a fresh history",
            exc,
        )
        return ReviewState()
    return state


def resolve_prior_state(
    *,
    prior_state: ReviewState | None,
    sticky_state: ReviewState,
) -> ReviewState:
    """Pick the state a round continues from.

    The loaded state wins whenever it carries anything at all. An empty one
    means this invocation found no artifact and no ledger entry, and the
    fallback is whatever an older lintro left behind in the sticky comment's
    own body. Every posting surface asks the same question, so they ask it
    here rather than each re-deriving the predicate.

    Args:
        prior_state: State already loaded for this invocation, if any.
        sticky_state: State recovered from the sticky comment's own body.

    Returns:
        ReviewState: The state to render and match against.
    """
    if prior_state is None or not (
        prior_state.coverage or prior_state.runs or prior_state.findings
    ):
        return sticky_state
    return prior_state


def persist_review_state(
    *,
    result: object,
    context: object,
    prior: ReviewState | None,
    pr_number: int | None,
    repo: str,
    inline_comment_ids: dict[str, int] | None = None,
) -> None:
    """Advance the state for this round and write it where the next one looks.

    Args:
        result: This round's review result. A value that is not a
            :class:`~lintro.ai.review.models.review_result.ReviewResult` is
            ignored, so a short-circuited run persists nothing.
        context: Review context carrying the base and head refs.
        prior: State this round continued from.
        pr_number: Pull request number, or ``None`` for a local branch run.
        repo: ``owner/name`` slug stamped onto the written state.
        inline_comment_ids: Finding key to inline comment id captured this
            round, so a later round can find those threads again.
    """
    from importlib.metadata import version as pkg_version

    if not isinstance(result, ReviewResult):
        return
    head_sha = str(getattr(context, "head_ref", "") or "")
    advanced = advance_review_state(
        request=StickyRequest(
            result=result,
            prior_state=prior,
            head_sha=head_sha,
            transport=result.metadata.transport,
            auth_mode=result.metadata.auth_mode,
            cost_basis=result.metadata.cost_basis,
            inline_comment_ids=inline_comment_ids,
            departed_paths=departed_paths(context=context),
        ),
    )
    state = replace(
        advanced,
        repo=repo,
        pr_number=pr_number,
        base_sha=str(getattr(context, "base_ref", "") or ""),
        head_sha=head_sha,
        workflow=_WORKFLOW,
        event=os.environ.get("GITHUB_EVENT_NAME", ""),
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        lintro_version=_lintro_version(pkg_version),
    )
    write_state_part(
        state=state,
        directory=state_dir(ci=_in_actions()),
        sequence=1,
        final=True,
    )
    if not _in_actions():
        write_local_state(
            state=state,
            key=local_ledger_key(pr_number=pr_number, head_ref=head_sha),
        )


def departed_paths(*, context: object) -> frozenset[str]:
    """Return the paths that left the diff (deletes and rename sources).

    Args:
        context: Review context carrying this round's changed files.

    Returns:
        frozenset[str]: Paths whose open findings may resolve because the code
        that raised them is no longer in the diff.
    """
    changed = getattr(context, "changed_files", ())
    departed: set[str] = set()
    for item in changed:
        status = item.status
        if not isinstance(status, ChangedFileStatus):
            try:
                status = ChangedFileStatus(str(status))
            except ValueError:
                continue
        if status is ChangedFileStatus.DELETED:
            departed.add(item.path)
        previous = getattr(item, "previous_path", None)
        if previous and status is ChangedFileStatus.RENAMED:
            departed.add(previous)
    return frozenset(departed)


def _lintro_version(pkg_version: Callable[[str], str]) -> str:
    """Return the installed lintro version, or an empty string.

    Args:
        pkg_version: Distribution-version lookup, injected so a test can drive
            the failure branch without touching the installed metadata.

    Returns:
        str: The version, or an empty string when it cannot be read.
    """
    try:
        return str(pkg_version("lintro"))
    except Exception:
        return ""
