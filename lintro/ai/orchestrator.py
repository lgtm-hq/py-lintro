"""AI orchestration for check/fix actions.

Thin coordinator that delegates to pipeline and rerun services.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from lintro.ai import require_ai
from lintro.ai.budget import CostBudget
from lintro.ai.display import render_summary, render_summary_annotations
from lintro.ai.display.shared import is_github_actions
from lintro.ai.filters import filter_issues
from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.metadata import attach_summary_metadata
from lintro.ai.models import AIFixSuggestion, AIResult, AISummary
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
) -> AIResult:
    """Run AI-powered enhancement for check/fix actions.

    Args:
        action: The action being performed (CHECK or FIX).
        all_results: Tool results from the linting run.
        lintro_config: Full lintro configuration.
        logger: Thread-safe console logger.
        output_format: Output format (e.g. "terminal", "json").
        ai_fix: Whether to generate AI fix suggestions.

    Returns:
        AIResult with structured outcome data for exit code decisions.

    Raises:
        KeyboardInterrupt: Re-raised immediately.
        SystemExit: Re-raised immediately.
        Exception: Re-raised when ``fail_on_ai_error`` is True.
    """
    try:
        require_ai()

        ai_config = lintro_config.ai
        workspace_root = resolve_workspace_root(lintro_config.config_path)
        provider = get_provider(ai_config)
        is_json = output_format.lower() == "json"

        # P5-4: Verbose — log provider, model, and workspace at info level
        if ai_config.verbose:
            loguru_logger.info(
                f"AI: provider={ai_config.provider.value}, "
                f"model={ai_config.model or 'default'}, "
                f"workspace_root={workspace_root}",
            )

        if action == Action.CHECK:
            return _run_ai_check(
                all_results=all_results,
                provider=provider,
                ai_config=ai_config,
                logger=logger,
                is_json=is_json,
                ai_fix=ai_fix,
                workspace_root=workspace_root,
            )
        elif action == Action.FIX:
            return _run_ai_fix(
                all_results=all_results,
                provider=provider,
                ai_config=ai_config,
                logger=logger,
                is_json=is_json,
                workspace_root=workspace_root,
            )
        return AIResult()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        if getattr(lintro_config.ai, "fail_on_ai_error", False):
            raise
        loguru_logger.debug(
            f"AI enhancement failed ({type(e).__name__}): {e}",
            exc_info=True,
        )
        logger.console_output(
            f"  AI: enhancement unavailable ({type(e).__name__})",
        )
        return AIResult(error=True)


def _run_ai_check(
    *,
    all_results: list[ToolResult],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    is_json: bool,
    ai_fix: bool,
    workspace_root: Path,
) -> AIResult:
    """Run AI summary and optional AI fix suggestions for check action."""
    budget = CostBudget(max_cost_usd=ai_config.max_cost_usd)

    summary = generate_summary(
        all_results,
        provider,
        max_tokens=ai_config.max_tokens,
        workspace_root=workspace_root,
        timeout=ai_config.api_timeout,
        max_retries=ai_config.max_retries,
        base_delay=ai_config.retry_base_delay,
        max_delay=ai_config.retry_max_delay,
        backoff_factor=ai_config.retry_backoff_factor,
        fallback_models=ai_config.fallback_models,
    )
    if summary and not is_json:
        output = render_summary(summary, show_cost=ai_config.show_cost_estimate)
        if output:
            logger.console_output(output)
        # Emit GitHub Actions annotations for summary insights
        if is_github_actions():
            annotations = render_summary_annotations(summary)
            if annotations:
                logger.console_output(annotations)

    if summary:
        budget.record(summary.cost_estimate)
        for result in all_results:
            if result.issues and not result.skipped:
                attach_summary_metadata(result, summary)

    # Post summary as PR comment when enabled
    if summary and ai_config.github_pr_comments:
        _post_pr_comments(summary=summary, logger=logger)

    if not ai_fix:
        return AIResult()

    all_fix_issues: list[tuple[ToolResult, BaseIssue]] = []
    for result in all_results:
        loguru_logger.debug(
            f"AI fix (chk): {result.name} "
            f"issues={len(result.issues) if result.issues else 0}",
        )
        if not result.issues or result.skipped:
            continue
        filtered = filter_issues(list(result.issues), ai_config)
        for issue in filtered:
            if _normalize_issue_path_for_workspace(
                issue=issue,
                workspace_root=workspace_root,
                cwd=result.cwd,
            ):
                all_fix_issues.append((result, issue))

    fixes_applied = 0
    fixes_failed = 0
    if all_fix_issues:
        fixes_applied, fixes_failed = run_fix_pipeline(
            fix_issues=all_fix_issues,
            provider=provider,
            ai_config=ai_config,
            logger=logger,
            output_format="json" if is_json else "terminal",
            workspace_root=workspace_root,
            budget=budget,
        )

    if not is_json:
        _log_fix_limit_message(
            logger=logger,
            total_issues=len(all_fix_issues),
            max_fix_issues=ai_config.max_fix_issues,
        )

    unfixed = len(all_fix_issues) - fixes_applied
    return AIResult(
        fixes_applied=fixes_applied,
        fixes_failed=fixes_failed,
        unfixed_issues=max(0, unfixed),
        budget_exceeded=(
            budget.remaining == 0.0 if budget.remaining is not None else False
        ),
    )


def _run_ai_fix(
    *,
    all_results: list[ToolResult],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    is_json: bool,
    workspace_root: Path,
) -> AIResult:
    """Run AI fix suggestions for format action."""
    budget = CostBudget(max_cost_usd=ai_config.max_cost_usd)

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
        remaining_issues = filter_issues(remaining_issues, ai_config)
        for issue in remaining_issues:
            if _normalize_issue_path_for_workspace(
                issue=issue,
                workspace_root=workspace_root,
                cwd=result.cwd,
            ):
                all_fix_issues.append((result, issue))

    fixes_applied = 0
    fixes_failed = 0
    if all_fix_issues:
        fixes_applied, fixes_failed = run_fix_pipeline(
            fix_issues=all_fix_issues,
            provider=provider,
            ai_config=ai_config,
            logger=logger,
            output_format="json" if is_json else "terminal",
            workspace_root=workspace_root,
            budget=budget,
        )

    if not is_json:
        _log_fix_limit_message(
            logger=logger,
            total_issues=len(all_fix_issues),
            max_fix_issues=ai_config.max_fix_issues,
        )

    unfixed = len(all_fix_issues) - fixes_applied
    return AIResult(
        fixes_applied=fixes_applied,
        fixes_failed=fixes_failed,
        unfixed_issues=max(0, unfixed),
        budget_exceeded=(
            budget.remaining == 0.0 if budget.remaining is not None else False
        ),
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
    if remaining_count > len(issues):
        loguru_logger.warning(
            f"remaining_issues_count ({remaining_count}) exceeds "
            f"issues length ({len(issues)}); clamping to {len(issues)}",
        )
        remaining_count = len(issues)
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


def _post_pr_comments(
    *,
    summary: AISummary | None = None,
    suggestions: list[AIFixSuggestion] | None = None,
    logger: ThreadSafeConsoleLogger,
) -> None:
    """Post AI findings as GitHub PR review comments.

    Logs a warning and continues gracefully on failure.

    Args:
        summary: Optional AI summary.
        suggestions: Optional fix suggestions.
        logger: Console logger.
    """
    reporter = GitHubPRReporter()
    if not reporter.is_available():
        loguru_logger.debug(
            "GitHub PR reporter not available — missing token, repo, or PR number",
        )
        return
    success = reporter.post_review_comments(
        suggestions=suggestions or [],
        summary=summary,
    )
    if success:
        loguru_logger.debug("GitHub PR review comments posted successfully")
    else:
        logger.console_output("  AI: failed to post some PR review comments")


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
