"""AI orchestration for check/fix actions.

Thin coordinator that delegates to pipeline and rerun services.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from lintro.ai import require_ai
from lintro.ai.display import render_summary
from lintro.ai.metadata import attach_summary_metadata
from lintro.ai.paths import resolve_workspace_file, resolve_workspace_root
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.providers import get_provider
from lintro.ai.summary import generate_summary
from lintro.enums.action import Action

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.config.lintro_config import LintroConfig
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue
    from lintro.utils.console.logger import ThreadSafeConsoleLogger


def run_ai_enhancement(
    *,
    action: Action,
    all_results: list[ToolResult],
    lintro_config: LintroConfig,
    logger: ThreadSafeConsoleLogger,
    output_format: str,
    ai_fix: bool = False,
) -> None:
    """Run AI-powered enhancement for check/fix actions."""
    try:
        require_ai()

        ai_config = lintro_config.ai
        workspace_root = resolve_workspace_root(lintro_config.config_path)
        provider = get_provider(ai_config)
        is_json = output_format.lower() == "json"

        if action == Action.CHECK:
            _run_ai_check(
                all_results=all_results,
                provider=provider,
                ai_config=ai_config,
                logger=logger,
                is_json=is_json,
                ai_fix=ai_fix,
                workspace_root=workspace_root,
            )
        elif action == Action.FIX:
            _run_ai_fix(
                all_results=all_results,
                provider=provider,
                ai_config=ai_config,
                logger=logger,
                is_json=is_json,
                workspace_root=workspace_root,
            )
    except Exception as e:
        loguru_logger.debug(f"AI enhancement failed: {e}", exc_info=True)
        logger.console_output("  AI: enhancement unavailable")


def _run_ai_check(
    *,
    all_results: list[ToolResult],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    is_json: bool,
    ai_fix: bool,
    workspace_root: Path,
) -> None:
    """Run AI summary and optional AI fix suggestions for check action."""
    summary = generate_summary(
        all_results,
        provider,
        max_tokens=ai_config.max_tokens,
        workspace_root=workspace_root,
    )
    if summary and not is_json:
        output = render_summary(summary, show_cost=ai_config.show_cost_estimate)
        if output:
            logger.console_output(output)

    if summary:
        for result in all_results:
            if result.issues and not result.skipped:
                attach_summary_metadata(result, summary)

    if not ai_fix:
        return

    all_fix_issues: list[tuple[ToolResult, BaseIssue]] = []
    for result in all_results:
        loguru_logger.debug(
            f"AI fix (chk): {result.name} "
            f"issues={len(result.issues) if result.issues else 0}",
        )
        if not result.issues or result.skipped:
            continue
        for issue in list(result.issues):
            if _normalize_issue_path_for_workspace(
                issue=issue,
                workspace_root=workspace_root,
                cwd=result.cwd,
            ):
                all_fix_issues.append((result, issue))

    if all_fix_issues:
        run_fix_pipeline(
            fix_issues=all_fix_issues,
            provider=provider,
            ai_config=ai_config,
            logger=logger,
            output_format="json" if is_json else "terminal",
            workspace_root=workspace_root,
        )

    if not is_json:
        _log_fix_limit_message(
            logger=logger,
            total_issues=len(all_fix_issues),
            max_fix_issues=ai_config.max_fix_issues,
        )


def _run_ai_fix(
    *,
    all_results: list[ToolResult],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    is_json: bool,
    workspace_root: Path,
) -> None:
    """Run AI fix suggestions for format action."""
    all_fix_issues: list[tuple[ToolResult, BaseIssue]] = []
    for result in all_results:
        loguru_logger.debug(
            f"AI: {result.name} skipped={result.skipped} "
            f"issues={type(result.issues).__name__} "
            f"len={len(result.issues) if result.issues else 0} "
            f"remaining={result.remaining_issues_count}",
        )
        if result.skipped:
            continue
        remaining_issues = _remaining_issues_for_fix_result(result)
        if not remaining_issues:
            continue
        for issue in remaining_issues:
            if _normalize_issue_path_for_workspace(
                issue=issue,
                workspace_root=workspace_root,
                cwd=result.cwd,
            ):
                all_fix_issues.append((result, issue))

    if all_fix_issues:
        run_fix_pipeline(
            fix_issues=all_fix_issues,
            provider=provider,
            ai_config=ai_config,
            logger=logger,
            output_format="json" if is_json else "terminal",
            workspace_root=workspace_root,
        )

    if not is_json:
        _log_fix_limit_message(
            logger=logger,
            total_issues=len(all_fix_issues),
            max_fix_issues=ai_config.max_fix_issues,
        )


def _remaining_issues_for_fix_result(result: ToolResult) -> list[BaseIssue]:
    """Return only issues still remaining after native fix step.

    In format mode, many tools include both initially detected and remaining
    issues in ``result.issues``. AI fix generation should only analyze the
    remaining tail to avoid stale suggestions that cannot apply.
    """
    if not result.issues:
        return []

    issues = list(result.issues)
    remaining_count = result.remaining_issues_count

    if remaining_count is None:
        return issues
    if remaining_count <= 0:
        return []
    if remaining_count >= len(issues):
        return issues

    # Convention: the remaining issues occupy the tail of the list.
    # Tools append all detected issues in order, so the last N are remaining.
    loguru_logger.debug(
        f"Tail-slicing {remaining_count} remaining issues from {len(issues)} total",
    )
    return issues[-remaining_count:]


def _normalize_issue_path_for_workspace(
    *,
    issue: BaseIssue,
    workspace_root: Path,
    cwd: str | None,
) -> bool:
    """Normalize issue path to an absolute workspace-local path."""
    if not issue.file:
        return False

    candidate = issue.file
    if cwd and not os.path.isabs(candidate):
        candidate = os.path.join(cwd, candidate)

    resolved = resolve_workspace_file(candidate, workspace_root)
    if resolved is None:
        loguru_logger.debug(
            f"Skipping issue outside workspace root: "
            f"file={candidate!r} root={workspace_root}",
        )
        return False

    issue.file = str(resolved)
    return True


def _log_fix_limit_message(
    *,
    logger: ThreadSafeConsoleLogger,
    total_issues: int,
    max_fix_issues: int,
) -> None:
    """Log a message when some issues were skipped due to the fix limit."""
    if total_issues <= max_fix_issues:
        return
    skipped = total_issues - max_fix_issues
    logger.console_output(
        f"\n  AI: analyzed {max_fix_issues} of "
        f"{total_issues} issues "
        f"({skipped} skipped due to limit)\n"
        f"   Increase ai.max_fix_issues in .lintro-config.yaml "
        f"to analyze more",
    )
