"""Issue filtering for AI processing."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.parsers.base_issue import BaseIssue


def should_process_issue(issue: BaseIssue, config: AIConfig) -> bool:
    """Check if an issue should be sent to AI based on path/rule filters.

    Evaluates include/exclude glob patterns for both file paths and rule
    codes. Include patterns act as an allowlist (only matching items are
    processed). Exclude patterns act as a denylist (matching items are
    skipped). When both include and exclude are set, include is checked
    first.

    Args:
        issue: The issue to evaluate.
        config: AI configuration containing filter patterns.

    Returns:
        True if the issue should be processed by AI, False otherwise.
    """
    file_path = getattr(issue, "file", "") or ""
    code = getattr(issue, "code", "") or ""

    # Path filtering
    if config.include_paths and not any(
        fnmatch.fnmatch(file_path, p) for p in config.include_paths
    ):
        return False
    if config.exclude_paths and any(
        fnmatch.fnmatch(file_path, p) for p in config.exclude_paths
    ):
        return False

    # Rule filtering
    if config.include_rules and not any(
        fnmatch.fnmatch(code, r) for r in config.include_rules
    ):
        return False
    return not (
        config.exclude_rules
        and any(fnmatch.fnmatch(code, r) for r in config.exclude_rules)
    )


def filter_issues(issues: list[BaseIssue], config: AIConfig) -> list[BaseIssue]:
    """Filter a list of issues based on AI path/rule configuration.

    Args:
        issues: List of issues to filter.
        config: AI configuration containing filter patterns.

    Returns:
        Filtered list containing only issues that pass all filters.
    """
    return [i for i in issues if should_process_issue(i, config)]
