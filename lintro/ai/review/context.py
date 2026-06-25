"""Git diff collection for AI code review."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext

_DIFF_FILE_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_NAME_STATUS_LINE = re.compile(
    r"^(?P<status>[A-Z][A-Z0-9]*)\s+(?P<path>.+?)(?:\s+(?P<old_path>.+))?$",
)
_NUMSTAT_LINE = re.compile(r"^(\d+|-)\s+(\d+|-)\s+(.+)$")


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
        uncommitted: When True, collect staged and unstaged working tree diffs.
        pr_number: Pull request number to review via ``gh``.
        repo: Optional ``owner/name`` repository for ``--pr`` mode.
        paths: Optional path prefixes to filter changed files and diff hunks.

    Returns:
        Parsed review context with unified diff and changed file metadata.

    Raises:
        ReviewContextError: When git/gh prerequisites fail or the diff is empty.
    """
    if pr_number is None:
        _ensure_git_repo()

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

    if not context.changed_files and not context.unified_diff.strip():
        raise ReviewContextError(
            "No changes found for review. Verify the diff range or path filters.",
            code=ReviewContextErrorCode.NO_CHANGES,
        )

    _validate_review_context_diff(context=context)

    return context


def _validate_review_context_diff(*, context: ReviewContext) -> None:
    """Ensure changed files align with parseable unified diff sections.

    Args:
        context: Collected review context.

    Raises:
        ReviewContextError: When changed files and diff content are inconsistent.
    """
    if not context.changed_files:
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
        if ref.startswith("refs/remotes/origin/"):
            return ref.removeprefix("refs/remotes/origin/")

    for candidate in ("main", "master", "develop"):
        verify = _run_git(args=["rev-parse", "--verify", candidate], check=False)
        if verify.returncode == 0:
            return candidate

    raise ReviewContextError(
        "Could not determine default branch. Pass --base explicitly.",
        code=ReviewContextErrorCode.DEFAULT_BRANCH_UNKNOWN,
    )


def parse_changed_files(*, name_status: str, numstat: str) -> list[ChangedFile]:
    """Parse ``git diff --name-status`` and ``--numstat`` output.

    Args:
        name_status: Raw output from ``git diff --name-status``.
        numstat: Raw output from ``git diff --numstat``.

    Returns:
        Parsed changed file entries.
    """
    stats: dict[str, tuple[int, int]] = {}
    unparsed_numstat: list[str] = []
    for line in numstat.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _NUMSTAT_LINE.match(stripped)
        if match is None:
            unparsed_numstat.append(stripped)
            continue
        additions_raw, deletions_raw, path = match.groups()
        additions = 0 if additions_raw == "-" else int(additions_raw)
        deletions = 0 if deletions_raw == "-" else int(deletions_raw)
        stats[path] = (additions, deletions)

    changed_files: list[ChangedFile] = []
    unparsed_name_status: list[str] = []
    for line in name_status.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _NAME_STATUS_LINE.match(stripped)
        if match is None:
            unparsed_name_status.append(stripped)
            continue
        status_code = match.group("status")
        path = match.group("path")
        old_path = match.group("old_path")
        normalized_status = _normalize_status(status_code=status_code)
        file_path = (
            old_path
            if normalized_status in {"renamed", "copied"} and old_path
            else path
        )
        additions, deletions = stats.get(file_path, stats.get(path, (0, 0)))
        changed_files.append(
            ChangedFile(
                path=file_path,
                status=normalized_status,
                additions=additions,
                deletions=deletions,
            ),
        )

    unparsed = [*unparsed_numstat, *unparsed_name_status]
    if unparsed:
        raise ReviewContextError(
            "Failed to parse git diff metadata: " f"{'; '.join(unparsed[:3])}",
            code=ReviewContextErrorCode.GIT_OUTPUT_PARSE_FAILED,
        )

    return changed_files


def split_unified_diff_by_file(*, unified_diff: str) -> dict[str, str]:
    """Split a unified diff into per-file diff sections.

    Args:
        unified_diff: Full unified diff text.

    Returns:
        Mapping of repository-relative file path to that file's diff section.
    """
    if not unified_diff.strip():
        return {}

    matches = list(_DIFF_FILE_HEADER.finditer(unified_diff))
    if not matches:
        return {}

    per_file: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(unified_diff)
        )
        section = unified_diff[start:end]
        _old_path, new_path = match.groups()
        per_file[new_path] = section

    return per_file


def _ensure_git_repo() -> None:
    """Verify the current directory is inside a git repository.

    Raises:
        ReviewContextError: When git is unavailable or not in a repository.
    """
    if shutil.which("git") is None:
        raise ReviewContextError(
            "git is not installed or not on PATH — required for lintro review.",
            code=ReviewContextErrorCode.GIT_UNAVAILABLE,
        )
    result = _run_git(args=["rev-parse", "--git-dir"], check=False)
    if result.returncode != 0:
        raise ReviewContextError(
            "Not a git repository — lintro review requires a git checkout.",
            code=ReviewContextErrorCode.NOT_GIT_REPO,
        )


def _run_git(
    *,
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git subprocess and return captured output.

    Args:
        args: Git command arguments (excluding ``git`` itself).
        check: When True, raise on non-zero exit codes.

    Returns:
        Completed process with stdout/stderr captured as text.

    Raises:
        ReviewContextError: When git execution fails and ``check`` is True.
    """
    try:
        result = subprocess.run(  # nosec B603 - fixed argv list; shell=False
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ReviewContextError(
            f"Failed to run git {' '.join(args)}: {exc}",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        ) from exc

    if check and result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ReviewContextError(
            f"git {' '.join(args)} failed: {stderr}",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        )

    return result


def _run_gh(*, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a GitHub CLI subprocess.

    Args:
        args: ``gh`` command arguments (excluding ``gh`` itself).

    Returns:
        Completed process with stdout/stderr captured as text.

    Raises:
        ReviewContextError: When ``gh`` is missing or the command fails.
    """
    if shutil.which("gh") is None:
        raise ReviewContextError(
            "GitHub CLI (gh) is not installed — required for --pr review mode.",
            code=ReviewContextErrorCode.GH_UNAVAILABLE,
        )
    try:
        result = subprocess.run(  # nosec B603 - fixed argv list; shell=False
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ReviewContextError(
            f"Failed to run gh {' '.join(args)}: {exc}",
            code=ReviewContextErrorCode.GH_COMMAND_FAILED,
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown gh error"
        raise ReviewContextError(
            f"gh {' '.join(args)} failed: {stderr}",
            code=ReviewContextErrorCode.GH_COMMAND_FAILED,
        )

    return result


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

    diff_range = f"{merge_base}...HEAD"
    unified_diff = _run_git(args=["diff", diff_range]).stdout
    changed_files = parse_changed_files(
        name_status=_run_git(args=["diff", "--name-status", diff_range]).stdout,
        numstat=_run_git(args=["diff", "--numstat", diff_range]).stdout,
    )
    head_ref = _run_git(args=["rev-parse", "HEAD"]).stdout.strip()

    return ReviewContext(
        base_ref=merge_base,
        head_ref=head_ref,
        changed_files=changed_files,
        unified_diff=unified_diff,
        pr_metadata=None,
    )


def _collect_uncommitted_context() -> ReviewContext:
    """Collect staged and unstaged working tree diffs against HEAD.

    Returns:
        Review context for the working tree and index.
    """
    unified_diff = _run_git(args=["diff", "HEAD"]).stdout
    changed_files = parse_changed_files(
        name_status=_run_git(args=["diff", "HEAD", "--name-status"]).stdout,
        numstat=_run_git(args=["diff", "HEAD", "--numstat"]).stdout,
    )
    head_ref = _run_git(args=["rev-parse", "HEAD"]).stdout.strip()

    return ReviewContext(
        base_ref="WORKTREE",
        head_ref=head_ref,
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
    """
    diff_args = ["pr", "diff", str(pr_number)]
    view_args = [
        "pr",
        "view",
        str(pr_number),
        "--json",
        "title,body,number,baseRefOid,headRefOid,repository",
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


def _parse_pr_view_json(
    *,
    payload: str,
    repo_override: str | None,
) -> tuple[PRMetadata, str, str]:
    """Parse ``gh pr view --json`` output into metadata and refs.

    Args:
        payload: JSON payload from ``gh pr view``.
        repo_override: Optional repository override from CLI flags.

    Returns:
        Tuple of PR metadata, base ref, and head ref.

    Raises:
        ReviewContextError: When repository metadata cannot be resolved or JSON
            is malformed.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ReviewContextError(
            f"Failed to parse gh pr view JSON: {exc}",
            code=ReviewContextErrorCode.GH_JSON_INVALID,
        ) from exc

    if not isinstance(data, dict):
        raise ReviewContextError(
            "gh pr view JSON root must be an object.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    number = data.get("number")
    if not isinstance(number, int) or number <= 0:
        raise ReviewContextError(
            "gh pr view JSON missing or invalid required field: 'number'.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    title = data.get("title")
    if not isinstance(title, str):
        raise ReviewContextError(
            "gh pr view JSON missing or invalid required field: 'title'.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    base_ref = data.get("baseRefOid")
    if not isinstance(base_ref, str) or not base_ref.strip():
        raise ReviewContextError(
            "gh pr view JSON missing or invalid required field: 'baseRefOid'.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    head_ref = data.get("headRefOid")
    if not isinstance(head_ref, str) or not head_ref.strip():
        raise ReviewContextError(
            "gh pr view JSON missing or invalid required field: 'headRefOid'.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise ReviewContextError(
            "gh pr view JSON missing or invalid required field: 'repository'.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    repo_name = repo_override or repository.get("nameWithOwner")
    if not isinstance(repo_name, str) or not repo_name.strip():
        raise ReviewContextError(
            "Could not determine repository for pull request review.",
            code=ReviewContextErrorCode.GH_METADATA_INVALID,
        )

    body = data.get("body")
    body_text = body if isinstance(body, str) else ""

    metadata = PRMetadata(
        title=title,
        body=body_text,
        number=number,
        repo=repo_name,
    )
    return metadata, base_ref, head_ref


def _parse_changed_files_from_diff(*, unified_diff: str) -> list[ChangedFile]:
    """Derive changed files from a unified diff when git metadata is unavailable.

    Args:
        unified_diff: Full unified diff text.

    Returns:
        Changed file entries inferred from diff hunks.
    """
    per_file = split_unified_diff_by_file(unified_diff=unified_diff)
    changed_files: list[ChangedFile] = []
    for path, diff_text in per_file.items():
        additions = sum(
            1
            for line in diff_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1
            for line in diff_text.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        changed_files.append(
            ChangedFile(
                path=path,
                status=_infer_status_from_diff_section(diff_text=diff_text),
                additions=additions,
                deletions=deletions,
            ),
        )
    return changed_files


def _infer_status_from_diff_section(*, diff_text: str) -> str:
    """Infer changed-file status from a unified diff section header."""
    if "new file mode" in diff_text:
        return "added"
    if "deleted file mode" in diff_text:
        return "deleted"
    if "rename from" in diff_text and "rename to" in diff_text:
        return "renamed"
    return "modified"


def _normalize_status(*, status_code: str) -> str:
    """Map git name-status codes to normalized review statuses.

    Args:
        status_code: Raw git status token (for example ``R100``).

    Returns:
        Normalized status label.
    """
    if status_code.startswith("A"):
        return "added"
    if status_code.startswith("D"):
        return "deleted"
    if status_code.startswith("R"):
        return "renamed"
    if status_code.startswith("C"):
        return "copied"
    if status_code.startswith("T"):
        return "type-changed"
    return "modified"


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
        if _path_matches_any_prefix(path=changed_file.path, prefixes=normalized_paths)
    ]
    per_file_diffs = split_unified_diff_by_file(unified_diff=context.unified_diff)
    filtered_diff_parts = [
        per_file_diffs[path]
        for path in sorted(per_file_diffs)
        if _path_matches_any_prefix(path=path, prefixes=normalized_paths)
    ]
    unified_diff = "".join(filtered_diff_parts)
    return ReviewContext(
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        changed_files=filtered_files,
        unified_diff=unified_diff,
        pr_metadata=context.pr_metadata,
    )


def _normalize_path_prefix(*, path: str) -> str:
    """Normalize a user path prefix for matching.

    Args:
        path: Raw user-supplied path prefix.

    Returns:
        Normalized POSIX-style prefix without leading or trailing slashes.
    """
    return path.replace("\\", "/").strip("/")


def _path_matches_any_prefix(*, path: str, prefixes: list[str]) -> bool:
    """Return True when ``path`` equals or is under any prefix.

    Args:
        path: Repository-relative file path.
        prefixes: Normalized path prefixes.

    Returns:
        True when the path matches any prefix.
    """
    normalized_path = path.replace("\\", "/").strip("/")
    for prefix in prefixes:
        if normalized_path == prefix or normalized_path.startswith(f"{prefix}/"):
            return True
    return False
