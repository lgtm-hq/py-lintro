"""Git diff collection for AI code review."""

from lintro.ai.review.context.collection import (
    collect_review_context,
    resolve_default_base_branch,
    validate_review_context_diff,
)
from lintro.ai.review.context.diff_parse import (
    parse_changed_files,
    split_unified_diff_by_file,
    unified_diff_preamble,
)

__all__ = [
    "collect_review_context",
    "parse_changed_files",
    "resolve_default_base_branch",
    "split_unified_diff_by_file",
    "unified_diff_preamble",
    "validate_review_context_diff",
]
