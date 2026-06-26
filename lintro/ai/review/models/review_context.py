"""Review diff context container."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata


@dataclass
class ReviewContext:
    """Collected diff context for an AI review run.

    Attributes:
        base_ref: Base commit OID or sentinel (``WORKTREE``) for the diff range.
        head_ref: Head commit OID for the diff range.
        changed_files: Parsed changed file entries.
        unified_diff: Full unified diff text for the selected range.
        pr_metadata: Optional PR metadata when reviewing a pull request.
        repo_root: Absolute path to the git repository root.
    """

    base_ref: str
    head_ref: str
    changed_files: list[ChangedFile]
    unified_diff: str
    pr_metadata: PRMetadata | None
    repo_root: str = ""
