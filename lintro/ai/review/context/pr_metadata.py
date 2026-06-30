"""Pull request metadata and diff-derived changed-file parsing."""

from __future__ import annotations

import json

from lintro.ai.review.context.diff_parse import (
    _count_diff_hunk_changes,
    _infer_status_from_diff_section,
    _previous_path_from_diff_section,
    split_unified_diff_by_file,
)
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata


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

    base_repository = data.get("baseRepository")
    head_repository = data.get("headRepository")

    parsed_head_repo: str | None = None
    if isinstance(head_repository, dict):
        raw_head_repo = head_repository.get("nameWithOwner")
        if isinstance(raw_head_repo, str) and raw_head_repo.strip():
            parsed_head_repo = raw_head_repo.strip()

    repo_name: str | None = None
    if isinstance(repo_override, str) and repo_override.strip():
        repo_name = repo_override.strip()
    elif isinstance(base_repository, dict):
        base_repo_name = base_repository.get("nameWithOwner")
        if isinstance(base_repo_name, str) and base_repo_name.strip():
            repo_name = base_repo_name

    if repo_name is None:
        if repo_override is not None:
            raise ReviewContextError(
                "Could not determine repository for pull request review.",
                code=ReviewContextErrorCode.GH_METADATA_INVALID,
            )
        if not isinstance(head_repository, dict):
            raise ReviewContextError(
                "gh pr view JSON missing or invalid required field: "
                "'headRepository'.",
                code=ReviewContextErrorCode.GH_METADATA_INVALID,
            )
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
        head_repo=parsed_head_repo,
    )
    return metadata, base_ref, head_ref


def _parse_changed_files_from_diff(*, unified_diff: str) -> list[ChangedFile]:
    """Derive changed files from a unified diff when git metadata is unavailable.

    Args:
        unified_diff: Full unified diff text.

    Returns:
        Changed file entries inferred from diff hunks.

    Raises:
        ReviewContextError: When changed-file metadata cannot be parsed from a
            diff section.
    """
    per_file = split_unified_diff_by_file(unified_diff=unified_diff)
    changed_files: list[ChangedFile] = []
    for path, diff_text in per_file.items():
        additions, deletions = _count_diff_hunk_changes(diff_text=diff_text)
        status = _infer_status_from_diff_section(diff_text=diff_text)
        try:
            changed_files.append(
                ChangedFile(
                    path=path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                    previous_path=_previous_path_from_diff_section(diff_text=diff_text),
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ReviewContextError(
                f"Failed to parse changed file metadata from diff for {path!r}: {exc}",
                code=ReviewContextErrorCode.GIT_OUTPUT_PARSE_FAILED,
            ) from exc
    return changed_files
