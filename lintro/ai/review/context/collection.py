"""Review context collection orchestration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from lintro.ai.review.context.diff_parse import (
    parse_changed_files,
    split_unified_diff_by_file,
    unified_diff_preamble,
)
from lintro.ai.review.context.git_ops import (
    _ensure_bash,
    _ensure_git_repo,
    _git_diff_triple_snapshot,
    _run_gh,
    _run_git,
)
from lintro.ai.review.context.pr_metadata import (
    _parse_changed_files_from_diff,
    _parse_pr_view_json,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext

_WORKFLOW_PATH_PREFIX = ".github/workflows/"


def collect_review_context(
    *,
    base: str | None = None,
    uncommitted: bool = False,
    pr_number: int | None = None,
    repo: str | None = None,
    paths: list[str] | None = None,
) -> ReviewContext:
    """Collect git diff context for review.

    Args:
        base: Base branch for ``merge-base`` three-dot diffs. When omitted,
            resolves the repository default branch via ``git symbolic-ref``.
        uncommitted: When True, collect staged and unstaged diffs against HEAD for
            tracked files. Untracked (never-added) files are excluded and logged.
        pr_number: Pull request number to review via ``gh``.
        repo: Optional ``owner/name`` repository for ``--pr`` mode.
        paths: Optional path prefixes to filter changed files and diff hunks.

    Returns:
        Parsed review context with unified diff and changed file metadata.

    Raises:
        ReviewContextError: When git/gh prerequisites fail or the diff is empty.
    """
    _validate_review_mode(
        base=base,
        uncommitted=uncommitted,
        pr_number=pr_number,
        repo=repo,
    )
    if pr_number is None:
        _ensure_bash()
        _ensure_git_repo()
        repo_root = _run_git(args=["rev-parse", "--show-toplevel"]).stdout.strip()
    else:
        repo_root = ""

    if pr_number is not None:
        context = _collect_pr_context(
            pr_number=pr_number,
            repo=repo,
        )
    elif uncommitted:
        context = _collect_uncommitted_context()
    else:
        resolved_base = base if base is not None else resolve_default_base_branch()
        context = _collect_branch_context(base=resolved_base)

    if paths:
        context = _filter_context_by_paths(context=context, paths=paths)

    context = _populate_post_image_files(context=context)

    if not context.changed_files and not context.unified_diff.strip():
        raise ReviewContextError(
            "No changes found for review. Verify the diff range or path filters.",
            code=ReviewContextErrorCode.NO_CHANGES,
        )

    validate_review_context_diff(context=context)

    return replace(context, repo_root=repo_root)


def validate_review_context_diff(*, context: ReviewContext) -> None:
    """Ensure changed files align with parseable unified diff sections.

    Args:
        context: Collected review context.

    Raises:
        ReviewContextError: When changed files and diff content are inconsistent.
    """
    if not context.changed_files:
        if context.unified_diff.strip():
            per_file_diffs = split_unified_diff_by_file(
                unified_diff=context.unified_diff,
            )
            if not per_file_diffs:
                raise ReviewContextError(
                    "Unified diff contains changes but no parseable file sections.",
                    code=ReviewContextErrorCode.NO_PARSEABLE_DIFF,
                )
            paths = ", ".join(per_file_diffs)
            raise ReviewContextError(
                "Unified diff contains file sections "
                f"({paths}) but no changed file metadata.",
                code=ReviewContextErrorCode.DIFF_DESYNC,
            )
        return

    if not context.unified_diff.strip():
        paths = ", ".join(changed_file.path for changed_file in context.changed_files)
        raise ReviewContextError(
            f"Changed files listed ({paths}) but unified diff is empty.",
            code=ReviewContextErrorCode.DIFF_DESYNC,
        )

    per_file_diffs = split_unified_diff_by_file(unified_diff=context.unified_diff)
    if not per_file_diffs:
        paths = ", ".join(changed_file.path for changed_file in context.changed_files)
        raise ReviewContextError(
            f"No parseable diff sections found for changed files: {paths}.",
            code=ReviewContextErrorCode.NO_PARSEABLE_DIFF,
        )

    missing = [
        changed_file.path
        for changed_file in context.changed_files
        if changed_file.path not in per_file_diffs
    ]
    if missing:
        raise ReviewContextError(
            "Changed files missing diff sections: " f"{', '.join(missing)}.",
            code=ReviewContextErrorCode.DIFF_DESYNC,
        )

    changed_paths = {changed_file.path for changed_file in context.changed_files}
    extra = [path for path in per_file_diffs if path not in changed_paths]
    if extra:
        raise ReviewContextError(
            "Diff sections missing changed-file metadata: " f"{', '.join(extra)}.",
            code=ReviewContextErrorCode.DIFF_DESYNC,
        )


def resolve_default_base_branch() -> str:
    """Resolve the repository default branch name.

    Returns:
        Default branch name detected from origin/HEAD or common local branches.

    Raises:
        ReviewContextError: When no default branch can be determined.
    """
    result = _run_git(
        args=["symbolic-ref", "refs/remotes/origin/HEAD"],
        check=False,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        if ref.startswith("refs/remotes/"):
            return ref.removeprefix("refs/remotes/")

    for candidate in ("main", "master", "develop"):
        for ref in (candidate, f"origin/{candidate}"):
            verify = _run_git(args=["rev-parse", "--verify", ref], check=False)
            if verify.returncode == 0:
                return ref

    raise ReviewContextError(
        "Could not determine default branch. Pass --base explicitly.",
        code=ReviewContextErrorCode.DEFAULT_BRANCH_UNKNOWN,
    )


def _validate_review_mode(
    *,
    base: str | None,
    uncommitted: bool,
    pr_number: int | None,
    repo: str | None,
) -> None:
    """Reject incompatible review context collection modes.

    Args:
        base: Optional explicit base branch for branch mode.
        uncommitted: Whether uncommitted mode was requested.
        pr_number: Optional pull request number for PR mode.
        repo: Optional ``owner/name`` repository for PR mode.

    Raises:
        ReviewContextError: When more than one collection mode is requested.
    """
    if uncommitted and base is not None:
        raise ReviewContextError(
            "Cannot combine uncommitted=True with an explicit base branch.",
            code=ReviewContextErrorCode.INVALID_REVIEW_MODE,
        )
    if pr_number is None:
        if repo is not None:
            raise ReviewContextError(
                "Cannot provide repo without pr_number.",
                code=ReviewContextErrorCode.INVALID_REVIEW_MODE,
            )
        return
    if uncommitted:
        raise ReviewContextError(
            "Cannot combine pr_number with uncommitted=True.",
            code=ReviewContextErrorCode.INVALID_REVIEW_MODE,
        )
    if base is not None:
        raise ReviewContextError(
            "Cannot combine pr_number with an explicit base branch.",
            code=ReviewContextErrorCode.INVALID_REVIEW_MODE,
        )


def _collect_branch_context(*, base: str) -> ReviewContext:
    """Collect diff context for ``merge-base(base, HEAD)...HEAD``.

    Args:
        base: Base branch name for merge-base resolution.

    Returns:
        Review context for commits on the current branch.

    Raises:
        ReviewContextError: When merge-base resolution fails.
    """
    merge_base = _run_git(args=["merge-base", base, "HEAD"]).stdout.strip()
    if not merge_base:
        raise ReviewContextError(
            f"Could not resolve merge-base between {base!r} and HEAD.",
            code=ReviewContextErrorCode.MERGE_BASE_FAILED,
        )

    head_ref = _run_git(args=["rev-parse", "HEAD"]).stdout.strip()
    diff_range = f"{merge_base}...{head_ref}"
    unified_diff, name_status, numstat = _git_diff_triple_snapshot(
        diff_args=[diff_range],
    )
    changed_files = parse_changed_files(
        name_status=name_status,
        numstat=numstat,
    )

    return ReviewContext(
        base_ref=merge_base,
        head_ref=head_ref,
        changed_files=changed_files,
        unified_diff=unified_diff,
        pr_metadata=None,
    )


def _collect_uncommitted_context() -> ReviewContext:
    """Collect staged and unstaged diffs against HEAD for tracked files.

    Untracked files are not included in ``git diff HEAD`` output. When present,
    a warning is logged so callers know the review scope is incomplete.

    Returns:
        Review context for the working tree and index.
    """
    untracked = _run_git(
        args=["ls-files", "--others", "--exclude-standard"],
    ).stdout.strip()
    if untracked:
        from loguru import logger

        untracked_lines = untracked.splitlines()
        sample = ", ".join(untracked_lines[:5])
        suffix = "..." if len(untracked_lines) > 5 else ""
        logger.warning(
            "Uncommitted review excludes {} untracked file(s): {}{}. "
            "Stage or commit new files to include them.",
            len(untracked_lines),
            sample,
            suffix,
        )

    head_ref = _run_git(args=["rev-parse", "HEAD"]).stdout.strip()
    unified_diff, name_status, numstat = _git_diff_triple_snapshot(
        diff_args=[head_ref],
    )
    changed_files = parse_changed_files(
        name_status=name_status,
        numstat=numstat,
    )

    return ReviewContext(
        base_ref=head_ref,
        head_ref="WORKTREE",
        changed_files=changed_files,
        unified_diff=unified_diff,
        pr_metadata=None,
    )


def _collect_pr_context(
    *,
    pr_number: int,
    repo: str | None,
) -> ReviewContext:
    """Collect diff context for a pull request via ``gh``.

    Args:
        pr_number: Pull request number.
        repo: Optional ``owner/name`` repository override.

    Returns:
        Review context including PR metadata.

    Raises:
        ReviewContextError: When gh metadata or diff retrieval fails.
    """
    diff_args = ["pr", "diff", str(pr_number)]
    view_args = [
        "pr",
        "view",
        str(pr_number),
        "--json",
        "title,body,number,baseRefOid,headRefOid,baseRepository,headRepository",
    ]
    if repo is not None:
        diff_args.extend(["--repo", repo])
        view_args.extend(["--repo", repo])

    pr_metadata: PRMetadata | None
    base_ref: str
    head_ref: str
    try:
        view_payload = _run_gh(args=view_args).stdout
        pr_metadata, base_ref, head_ref = _parse_pr_view_json(
            payload=view_payload,
            repo_override=repo,
        )
    except ReviewContextError as exc:
        raise ReviewContextError(
            f"Failed to load pull request metadata for #{pr_number}: {exc}",
            code=exc.code,
        ) from exc

    unified_diff = _run_gh(args=diff_args).stdout
    changed_files = _parse_changed_files_from_diff(unified_diff=unified_diff)

    return ReviewContext(
        base_ref=base_ref,
        head_ref=head_ref,
        changed_files=changed_files,
        unified_diff=unified_diff,
        pr_metadata=pr_metadata,
    )


def _populate_post_image_files(*, context: ReviewContext) -> ReviewContext:
    """Read full workflow file contents at head for changed workflow paths."""
    workflow_paths = [
        changed_file.path
        for changed_file in context.changed_files
        if changed_file.path.startswith(_WORKFLOW_PATH_PREFIX)
        and changed_file.status is not ChangedFileStatus.DELETED
    ]
    if not workflow_paths:
        return context

    post_image_files: dict[str, str] = {}
    head_repo: str | None = None
    if context.pr_metadata is not None:
        head_repo = context.pr_metadata.head_repo or context.pr_metadata.repo
    for path in workflow_paths:
        content = _read_workflow_post_image(
            path=path,
            head_ref=context.head_ref,
            repo=head_repo,
        )
        if content is not None:
            post_image_files[path] = content

    if not post_image_files:
        return context

    return ReviewContext(
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        changed_files=context.changed_files,
        unified_diff=context.unified_diff,
        pr_metadata=context.pr_metadata,
        post_image_files=post_image_files,
    )


def _read_workflow_post_image(
    *,
    path: str,
    head_ref: str,
    repo: str | None = None,
) -> str | None:
    """Return workflow file content at head when readable."""
    if head_ref == "WORKTREE":
        try:
            repo_root = Path(
                _run_git(args=["rev-parse", "--show-toplevel"]).stdout.strip(),
            )
            file_path = repo_root / path
            if not file_path.is_file():
                return None
            return file_path.read_text(encoding="utf-8", errors="surrogateescape")
        except (OSError, ReviewContextError):
            return None

    content: str | None = None
    try:
        result = _run_git(args=["show", f"{head_ref}:{path}"], check=False)
        if result.returncode == 0:
            # A zero exit means the path exists at head; preserve empty stdout
            # as a real empty file rather than falling back to stale diff hunks.
            content = result.stdout
    except ReviewContextError:
        content = None

    if content is not None:
        return content
    if repo is not None:
        return _read_workflow_post_image_via_gh(
            path=path,
            head_ref=head_ref,
            repo=repo,
        )
    return None


def _read_workflow_post_image_via_gh(
    *,
    path: str,
    head_ref: str,
    repo: str,
) -> str | None:
    """Fetch workflow file content from GitHub when local git objects are missing."""
    try:
        encoded_path = quote(path, safe="/")
        encoded_ref = quote(head_ref, safe="")
        result = _run_gh(
            args=[
                "api",
                f"repos/{repo}/contents/{encoded_path}?ref={encoded_ref}",
                "-H",
                "Accept: application/vnd.github.raw",
            ],
        )
    except ReviewContextError:
        return None
    return result.stdout


def _filter_context_by_paths(
    *,
    context: ReviewContext,
    paths: list[str],
) -> ReviewContext:
    """Filter changed files and diff hunks to the requested path prefixes.

    Args:
        context: Source review context.
        paths: Path prefixes to retain.

    Returns:
        Filtered review context.
    """
    normalized_paths = [_normalize_path_prefix(path=path) for path in paths]
    filtered_files = [
        changed_file
        for changed_file in context.changed_files
        if _changed_file_matches_any_prefix(
            changed_file=changed_file,
            prefixes=normalized_paths,
        )
    ]
    per_file_diffs = split_unified_diff_by_file(unified_diff=context.unified_diff)
    filtered_paths = {changed_file.path for changed_file in filtered_files}
    filtered_diff_parts = [
        diff_text
        for path, diff_text in per_file_diffs.items()
        if path in filtered_paths
    ]
    preamble = (
        unified_diff_preamble(unified_diff=context.unified_diff)
        if filtered_diff_parts
        else ""
    )
    unified_diff = preamble + "".join(filtered_diff_parts)
    filtered_post_image = {
        path: content
        for path, content in context.post_image_files.items()
        if path in filtered_paths
    }
    return ReviewContext(
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        changed_files=filtered_files,
        unified_diff=unified_diff,
        pr_metadata=context.pr_metadata,
        post_image_files=filtered_post_image,
    )


def _changed_file_matches_any_prefix(
    *,
    changed_file: ChangedFile,
    prefixes: list[str],
) -> bool:
    """Return True when a changed file's current or previous path matches."""
    candidate_paths = [changed_file.path]
    if changed_file.previous_path is not None:
        candidate_paths.append(changed_file.previous_path)
    return any(
        _path_matches_any_prefix(path=path, prefixes=prefixes)
        for path in candidate_paths
    )


def _normalize_path_prefix(*, path: str) -> str:
    """Normalize a user path prefix for matching.

    Args:
        path: Raw user-supplied path prefix.

    Returns:
        Normalized POSIX-style prefix without leading ``./`` or trailing slashes.
    """
    normalized = path.replace("\\", "/")
    if normalized in {".", "./", "/"}:
        return ""
    while True:
        previous = normalized
        while normalized.startswith("/"):
            normalized = normalized.lstrip("/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized == previous:
            break
    if normalized in {".", ""}:
        return ""
    while normalized.endswith("/") and len(normalized) > 1:
        normalized = normalized[:-1]
    return normalized


def _path_matches_any_prefix(*, path: str, prefixes: list[str]) -> bool:
    """Return True when ``path`` equals or is under any prefix.

    Args:
        path: Repository-relative file path.
        prefixes: Normalized path prefixes.

    Returns:
        True when the path matches any prefix.
    """
    normalized_path = path.replace("\\", "/")
    for prefix in prefixes:
        if (
            prefix == ""
            or normalized_path == prefix
            or normalized_path.startswith(
                f"{prefix}/",
            )
        ):
            return True
    return False
