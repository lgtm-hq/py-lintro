"""Build a PR diff from verified commits after a GitHub size refusal."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from lintro.ai.review.context.diff_parse import parse_changed_files
from lintro.ai.review.context.git_ops import (
    TimeoutExpired,
    _ensure_git_repo,
    _git_diff_triple_snapshot,
    _run_gh,
    _run_git,
    fetch_pr_head,
)
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext

_FETCH_REMEDY = (
    "Use a git checkout with origin able to fetch the pull request and git "
    "credentials available (actions/checkout persist-credentials: true). "
    "A gh token alone does not authenticate git fetch."
)


def _commit_oid(value: object) -> str:
    """Validate an exact commit SHA before using it as a git argument.

    Args:
        value: Commit OID supplied by GitHub.

    Returns:
        The validated SHA.

    Raises:
        ValueError: When the value is not a full SHA.
    """
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise ValueError("GitHub returned no usable commit SHA")
    return value


def _compare_merge_base(*, metadata: PRMetadata, base: str, head: str) -> str:
    """Resolve the merge base using GitHub's commit-addressed compare API.

    Args:
        metadata: PR repository and head owner.
        base: Base commit SHA.
        head: Head commit SHA.

    Returns:
        The exact merge-base commit SHA.

    Raises:
        ValueError: When the response contains no usable merge base or owner.
    """
    if not metadata.head_owner.strip():
        raise ValueError("compare lookup has no headRepositoryOwner login")
    endpoint = (
        f"repos/{metadata.repo}/compare/{_commit_oid(base)}..."
        f"{quote(metadata.head_owner, safe='')}:{_commit_oid(head)}"
    )
    payload = json.loads(_run_gh(args=["api", endpoint]).stdout)
    merge_base = payload.get("merge_base_commit") if isinstance(payload, dict) else None
    sha = merge_base.get("sha") if isinstance(merge_base, dict) else None
    return _commit_oid(sha)


def _ensure_merge_base(sha: str) -> None:
    """Fetch a missing merge-base commit, preserving full clones.

    Args:
        sha: Verified merge-base SHA from the compare API.
    """
    commit = f"{sha}^{{commit}}"
    if _run_git(args=["cat-file", "-e", commit], check=False).returncode != 0:
        shallow = _run_git(args=["rev-parse", "--is-shallow-repository"])
        depth = ["--depth", "1"] if shallow.stdout.strip() == "true" else []
        _run_git(args=["fetch", "--no-tags", *depth, "origin", sha])
    _run_git(args=["cat-file", "-e", commit])


# At most eight fetches and 4096 additional generations; each git command also
# retains the shared 120-second timeout.
_HEAD_DEEPEN_STEPS = (32, 64, 128, 256, 512, 1024, 2048, 32)


def _head_history_is_shallow(head: str) -> bool:
    """Check for a shallow boundary reachable from this head.

    Args:
        head: Verified PR head SHA.

    Returns:
        Whether head ancestry is truncated, ignoring unrelated shallow refs.
    """
    shallow_path = Path(
        _run_git(args=["rev-parse", "--git-path", "shallow"]).stdout.strip(),
    )
    if not shallow_path.exists():
        return False
    boundaries = set(shallow_path.read_text().splitlines())
    roots = _run_git(args=["rev-list", "--max-parents=0", head]).stdout.splitlines()
    return bool(boundaries.intersection(roots))


def _verify_head_ancestry(*, merge_base: str, head: str) -> None:
    """Deepen truncated head history within a fixed budget before deciding.

    Args:
        merge_base: Compare API merge base.
        head: Verified PR head SHA.

    Raises:
        ReviewContextError: If ancestry is disproven or remains unverified.
    """
    steps = iter(_HEAD_DEEPEN_STEPS)
    while (
        _run_git(
            args=["merge-base", "--is-ancestor", merge_base, head],
            check=False,
        ).returncode
        != 0
    ):
        if not _head_history_is_shallow(head):
            raise ReviewContextError(
                "Compare merge base is not an ancestor of the head. "
                "Retry the compare lookup; the merge base must be an ancestor "
                "of the verified head",
                code=ReviewContextErrorCode.MERGE_BASE_FAILED,
            )
        depth = next(steps, None)
        if depth is None:
            raise ReviewContextError(
                "Compare merge base: could not verify within the shallow "
                "history available. Check out with full history (fetch-depth: 0)",
                code=ReviewContextErrorCode.MERGE_BASE_FAILED,
            )
        _run_git(args=["fetch", "--no-tags", f"--deepen={depth}", "origin", head])


def build_local_pr_diff(
    *,
    metadata: PRMetadata,
    base: str,
    head: str,
    refusal: ReviewContextError,
) -> ReviewContext:
    """Collect the explicit merge-base/head snapshot or fail with both causes.

    Args:
        metadata: Metadata from the single PR view request.
        base: Base commit SHA reported by GitHub.
        head: Head commit SHA reported by GitHub.
        refusal: The original GitHub size refusal.

    Returns:
        Context containing the locally built PR diff.

    Raises:
        ReviewContextError: When any fallback step fails, with a remedy.
    """
    remedy = _FETCH_REMEDY
    try:
        _ensure_git_repo()
        remedy = (
            "Retry the compare lookup when GitHub is reachable; ensure the response "
            "contains a usable merge_base_commit.sha."
        )
        merge_base = _compare_merge_base(metadata=metadata, base=base, head=head)
        remedy = _FETCH_REMEDY
        fetch_pr_head(pr_number=metadata.number, head_oid=head, fallback=True)
        remedy = "Retry with a checkout able to fetch the merge-base commit."
        _ensure_merge_base(merge_base)
        remedy = "Check out with full history (fetch-depth: 0) and retry."
        _verify_head_ancestry(merge_base=merge_base, head=head)
        remedy = "Retry with a checkout whose git diff can read both verified commits."
        unified_diff, name_status, numstat = _git_diff_triple_snapshot(
            diff_args=[merge_base, head],
        )
        changed_files = parse_changed_files(name_status=name_status, numstat=numstat)
        if metadata.changed_files and not changed_files:
            raise ReviewContextError(
                "GitHub reports changed files but the local diff is empty.",
                code=ReviewContextErrorCode.DIFF_DESYNC,
            )
        return ReviewContext(
            base_ref=merge_base,
            head_ref=head,
            changed_files=changed_files,
            unified_diff=unified_diff,
            pr_metadata=metadata,
            diff_source="local-git",
        )
    except Exception as exc:
        # This boundary also converts unexpected reconstruction failures into the
        # context-error contract; no unverified diff may reach a review.
        if isinstance(exc, ReviewContextError):
            if exc.code is ReviewContextErrorCode.PR_LOCAL_DIFF_FAILED:
                remedy = (
                    "Retry with current pull request metadata after the head moved."
                )
            elif exc.code is ReviewContextErrorCode.MERGE_BASE_FAILED:
                remedy = ""
            elif exc.code is ReviewContextErrorCode.GIT_COMMAND_FAILED and isinstance(
                exc.__cause__,
                TimeoutExpired,
            ):
                remedy = "Retry the git command when the repository and remote respond."
        raise ReviewContextError(
            f"GitHub refused the pull request diff for size ({refusal}). "
            f"The local diff could not be built: {exc}. {remedy}",
            code=ReviewContextErrorCode.PR_LOCAL_DIFF_FAILED,
        ) from None
