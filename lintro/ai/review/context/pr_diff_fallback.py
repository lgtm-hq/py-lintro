"""Build a PR diff from verified commits after a GitHub size refusal."""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from lintro.ai.review.context.diff_parse import parse_changed_files
from lintro.ai.review.context.git_ops import (
    _ensure_git_repo,
    _git_diff_triple_snapshot,
    _run_gh,
    _run_git,
)
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.pr_head import fetch_pr_head

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
        _run_git(args=["fetch", "--no-tags", "--depth", "1", "origin", sha])
    _run_git(args=["cat-file", "-e", commit])


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
    try:
        _ensure_git_repo()
        merge_base = _compare_merge_base(metadata=metadata, base=base, head=head)
        fetch_pr_head(pr_number=metadata.number, head_oid=head)
        _ensure_merge_base(merge_base)
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
        raise ReviewContextError(
            f"GitHub refused the pull request diff for size ({refusal}). "
            f"The local diff could not be built: {exc}. {_FETCH_REMEDY} "
            "For a compare lookup failure or timeout, retry when GitHub and git "
            "are reachable; ensure compare returns merge_base_commit.sha. "
            "If the head moved, retry with current pull request metadata.",
            code=ReviewContextErrorCode.PR_LOCAL_DIFF_FAILED,
        ) from None
