"""What every commit of a range adds and deletes (#3449).

The lineage fallback (:func:`~lintro.ai.review.rename_lineage.lineage_rename`)
moves a finding by the pull request's rename metadata, a similarity guess over
the whole pull request. The endpoints of a round's range can hide a file that
was created and renamed away inside it, so the fallback reads the range commit
by commit instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.git_line_map import _range_is_complete

__all__ = ["RangeChurn", "range_churn"]


@dataclass(frozen=True, slots=True)
class RangeChurn:
    """Paths any commit of a range adds or deletes, renames read as both (#3449).

    Attributes:
        added: Paths some commit adds.
        deleted: Paths some commit deletes.
    """

    added: frozenset[str]
    deleted: frozenset[str]


def range_churn(*, repo_root: str, from_sha: str, to_sha: str) -> RangeChurn | None:
    """List the paths every commit of ``from_sha..to_sha`` adds or deletes.

    Walks each commit, not the endpoints, with rename detection off, so a
    file created and renamed away inside the range still shows; merges are
    compared with each parent.

    Args:
        repo_root: The repository.
        from_sha: The older commit (excluded); must be an ancestor of
            ``to_sha``.
        to_sha: The newer commit.

    Returns:
        The churn, or ``None`` when ``from_sha`` is not an ancestor, the range
        may be cut by a shallow boundary, or git could not answer.
    """
    if not from_sha or not to_sha:
        return None
    try:
        ancestor = _run_git(
            args=["-C", repo_root, "merge-base", "--is-ancestor", from_sha, to_sha],
            check=False,
        )
        if ancestor.returncode != 0 or not _range_is_complete(
            repo_root=repo_root,
            from_sha=from_sha,
            to_sha=to_sha,
        ):
            return None
        result = _run_git(
            args=[
                "-C",
                repo_root,
                "-c",
                "core.quotePath=false",
                "log",
                "--format=",
                "--name-status",
                "-z",
                "-m",
                "--no-renames",
                "--diff-filter=AD",
                "--no-ext-diff",
                "--no-textconv",
                "--no-relative",
                f"{from_sha}..{to_sha}",
            ],
            check=False,
        )
    except ReviewContextError as exc:
        logger.debug("Could not walk {}..{}: {}", from_sha, to_sha, exc)
        return None
    if result.returncode != 0:
        return None
    found: dict[str, set[str]] = {"A": set(), "D": set()}
    tokens = iter(result.stdout.split("\0"))
    for status in tokens:
        if status.strip() in found:
            found[status.strip()].add(next(tokens, ""))
    return RangeChurn(
        added=frozenset(found["A"] - {""}),
        deleted=frozenset(found["D"] - {""}),
    )
