"""Review diff context container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lintro.ai.review.enums.review_checkout import ReviewCheckout

if TYPE_CHECKING:
    from lintro.ai.review.pr_head import PrHeadWorktree
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.skipped_file import SkippedFile


@dataclass
class ReviewContext:
    """Collected diff context for an AI review run.

    Attributes:
        base_ref: Base commit OID or sentinel (``WORKTREE``) for the diff range.
        head_ref: Head commit OID for the diff range.
        changed_files: Parsed changed file entries.
        unified_diff: Full unified diff text for the selected range.
        pr_metadata: Optional PR metadata when reviewing a pull request.
        post_image_files: Full post-change contents for changed workflow files
            keyed by repository-relative path.
        repo_root: Absolute path to the git repository root. On ``--pr`` it
            is the PR head's temporary worktree (#2733).
        head_worktree: The temporary worktree checked out at the PR head
            (#2733), removed when the run ends; ``None`` off ``--pr`` or when
            no tree is available (``checkout`` is then ``NONE``).
        skipped_files: Changed files dropped during context collection, each
            carrying why it was dropped. Reported on the per-review comment so
            a narrowed review scope is visible rather than implied (#1910).
        checkout: Which side of the range the working tree holds, so the
            git-native prompt can say what a disk read means (#2685).
    """

    base_ref: str
    head_ref: str
    changed_files: list[ChangedFile]
    unified_diff: str
    pr_metadata: PRMetadata | None = None
    post_image_files: dict[str, str] = field(default_factory=dict)
    repo_root: str = ""
    head_worktree: PrHeadWorktree | None = None
    skipped_files: list[SkippedFile] = field(default_factory=list)
    checkout: ReviewCheckout = ReviewCheckout.UNKNOWN
