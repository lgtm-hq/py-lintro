"""AI orchestration for check/fix actions."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from lintro.ai import require_ai
from lintro.ai.display import render_summary, render_validation
from lintro.ai.fix import generate_fixes
from lintro.ai.interactive import apply_fixes, review_fixes_interactive
from lintro.ai.metadata import (
    attach_fix_suggestions_metadata,
    attach_fixed_count_metadata,
    attach_summary_metadata,
    attach_validation_counts_metadata,
)
from lintro.ai.paths import resolve_workspace_file, resolve_workspace_root
from lintro.ai.providers import get_provider
from lintro.ai.risk import is_safe_style_fix
from lintro.ai.summary import generate_post_fix_summary, generate_summary
from lintro.ai.validation import validate_applied_fixes
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult


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
        f"\n  AI analyzed {max_fix_issues} of "
        f"{total_issues} remaining issues "
        f"({skipped} skipped due to limit)\n"
        f"   Increase ai.max_fix_issues in .lintro-config.yaml "
        f"to analyze more",
    )


if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.models import AIFixSuggestion
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.config.lintro_config import LintroConfig
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
        logger.console_output(f"AI: enhancement unavailable ({e})")


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
            all_fix_issues.append((result, issue))

    if all_fix_issues:
        _run_ai_fix_combined(
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
            all_fix_issues.append((result, issue))

    if all_fix_issues:
        _run_ai_fix_combined(
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
        f"Tail-slicing {remaining_count} remaining issues " f"from {len(issues)} total",
    )
    return issues[-remaining_count:]


def _run_ai_fix_combined(
    *,
    fix_issues: list[tuple[ToolResult, BaseIssue]],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    output_format: str,
    workspace_root: Path,
) -> None:
    """Generate and optionally apply AI fix suggestions across all tools."""
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]] = {}
    for result, issue in fix_issues:
        if result.name not in by_tool:
            by_tool[result.name] = (result, [])
        by_tool[result.name][1].append(issue)

    all_suggestions: list[AIFixSuggestion] = []
    remaining_budget = ai_config.max_fix_issues

    for tool_name, (result, issues) in by_tool.items():
        if remaining_budget <= 0:
            break

        normalized_issues: list[BaseIssue] = []
        for issue in issues:
            if _normalize_issue_path_for_workspace(
                issue=issue,
                workspace_root=workspace_root,
                cwd=result.cwd,
            ):
                normalized_issues.append(issue)

        if not normalized_issues:
            by_tool[tool_name] = (result, [])
            continue
        by_tool[tool_name] = (result, normalized_issues)

        loguru_logger.debug(
            f"AI fix: {tool_name} has {len(normalized_issues)} issues, "
            f"budget={remaining_budget}",
        )

        suggestions = generate_fixes(
            normalized_issues,
            provider,
            tool_name=tool_name,
            max_issues=remaining_budget,
            max_workers=ai_config.max_parallel_calls,
            workspace_root=workspace_root,
            max_tokens=ai_config.max_tokens,
            max_retries=ai_config.max_retries,
            timeout=ai_config.api_timeout,
        )
        for suggestion in suggestions:
            if not suggestion.tool_name:
                suggestion.tool_name = tool_name
        remaining_budget -= len(normalized_issues[:remaining_budget])
        all_suggestions.extend(suggestions)

        if suggestions:
            attach_fix_suggestions_metadata(result, suggestions)

    if not all_suggestions:
        return

    applied = 0
    rejected = 0
    is_json = output_format.lower() == "json"
    applied_suggestions: list[AIFixSuggestion] = []
    safe_suggestions = [s for s in all_suggestions if is_safe_style_fix(s)]
    risky_suggestions = [s for s in all_suggestions if not is_safe_style_fix(s)]
    safe_failed = 0
    safe_fast_path_applied = False

    # Fast path: auto-apply deterministic style-only fixes when non-interactive.
    if (
        ai_config.auto_apply_safe_fixes
        and safe_suggestions
        and (is_json or not sys.stdin.isatty())
    ):
        safe_fast_path_applied = True
        applied_safe = apply_fixes(
            safe_suggestions,
            workspace_root=workspace_root,
        )
        applied_suggestions.extend(applied_safe)
        applied += len(applied_safe)
        safe_failed = len(safe_suggestions) - len(applied_safe)
        if safe_failed and not is_json:
            logger.console_output(
                f"  AI: safe-style auto-apply failed for {safe_failed} " "suggestions",
            )
        if not is_json:
            logger.console_output(
                f"  AI: auto-applied safe style fixes "
                f"{len(applied_safe)}/{len(safe_suggestions)}",
            )

    if ai_config.auto_apply:
        auto_apply_candidates = (
            risky_suggestions if safe_fast_path_applied else all_suggestions
        )
        auto_applied = apply_fixes(
            auto_apply_candidates,
            workspace_root=workspace_root,
        )
        applied_suggestions.extend(auto_applied)
        applied += len(auto_applied)
        rejected = len(auto_apply_candidates) - len(auto_applied) + safe_failed
        logger.console_output(
            f"  AI: auto-applied {applied}/{len(all_suggestions)} fixes",
        )
    elif not is_json:
        review_candidates = (
            risky_suggestions if safe_fast_path_applied else all_suggestions
        )
        accepted_count, rejected_count, interactive_applied = review_fixes_interactive(
            review_candidates,
            validate_after_group=ai_config.validate_after_group,
            workspace_root=workspace_root,
        )
        applied += accepted_count
        rejected += rejected_count + safe_failed
        applied_suggestions.extend(interactive_applied)

    fresh_remaining_results: list[ToolResult] = []
    if applied_suggestions:
        fresh_remaining_results = _rerun_tools_for_post_summary(by_tool)
        if fresh_remaining_results:
            _apply_rerun_results_to_original_results(
                by_tool=by_tool,
                rerun_results=fresh_remaining_results,
            )

    validation = None
    if applied_suggestions:
        validation = validate_applied_fixes(applied_suggestions)
        if (
            not is_json
            and validation
            and (validation.verified or validation.unverified)
        ):
            val_output = render_validation(validation)
            if val_output:
                logger.console_output(val_output)

    applied_by_tool: dict[str, int] = {}
    for suggestion in applied_suggestions:
        if not suggestion.tool_name:
            continue
        applied_by_tool[suggestion.tool_name] = (
            applied_by_tool.get(suggestion.tool_name, 0) + 1
        )
    for tool_name, (result, _issues) in by_tool.items():
        attach_fixed_count_metadata(
            result=result,
            fixed_count=applied_by_tool.get(tool_name, 0),
        )
        attach_validation_counts_metadata(
            result,
            verified_count=(
                validation.verified_by_tool.get(tool_name, 0) if validation else 0
            ),
            unverified_count=(
                validation.unverified_by_tool.get(tool_name, 0) if validation else 0
            ),
        )

    if (applied > 0 or rejected > 0) and not is_json:
        applied_for_summary = applied
        if validation and (validation.verified or validation.unverified):
            applied_for_summary = validation.verified

        if applied_suggestions:
            unique_results = fresh_remaining_results
            if not unique_results:
                logger.console_output(
                    "  AI: post-fix summary skipped "
                    "(fresh rerun unavailable for verification)",
                )
        else:
            unique_results = _unique_results_from_fix_issues(fix_issues)

        if unique_results:
            post_summary = generate_post_fix_summary(
                applied=applied_for_summary,
                rejected=rejected,
                remaining_results=unique_results,
                provider=provider,
                max_tokens=ai_config.max_tokens,
                workspace_root=workspace_root,
            )
            if post_summary:
                output = render_summary(
                    post_summary,
                    show_cost=ai_config.show_cost_estimate,
                )
                if output:
                    logger.console_output(output)


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


def _unique_results_from_fix_issues(
    fix_issues: list[tuple[ToolResult, BaseIssue]],
) -> list[ToolResult]:
    """Return unique tool results preserving first-seen order."""
    remaining_results = [result for result, _ in fix_issues]
    seen: set[str] = set()
    unique_results: list[ToolResult] = []
    for result in remaining_results:
        if result.name in seen:
            continue
        seen.add(result.name)
        unique_results.append(result)
    return unique_results


def _apply_rerun_results_to_original_results(
    *,
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
    rerun_results: list[ToolResult],
) -> None:
    """Apply fresh rerun issue counts back to original FIX results."""
    rerun_by_name = {result.name: result for result in rerun_results}

    for tool_name, (result, _issues) in by_tool.items():
        rerun = rerun_by_name.get(tool_name)
        if rerun is None:
            continue

        refreshed_issues = list(rerun.issues) if rerun.issues is not None else []
        result.issues = refreshed_issues
        result.issues_count = len(refreshed_issues)
        result.remaining_issues_count = len(refreshed_issues)
        result.success = rerun.success
        if rerun.output is not None:
            result.output = rerun.output


def _paths_for_rerun_context(
    *,
    file_paths: list[str],
    cwd: str | None,
) -> list[str]:
    """Prefer paths relative to tool cwd when possible."""
    if not cwd:
        return file_paths

    try:
        cwd_path = Path(cwd).resolve()
    except OSError:
        return file_paths

    contextual_paths: list[str] = []
    for file_path in file_paths:
        try:
            resolved = Path(file_path).resolve()
        except OSError:
            contextual_paths.append(file_path)
            continue
        try:
            contextual_paths.append(str(resolved.relative_to(cwd_path)))
        except ValueError:
            contextual_paths.append(str(resolved))
    return contextual_paths


_rerun_cwd_lock = threading.Lock()


def _rerun_tools_for_post_summary(
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
) -> list[ToolResult]:
    """Re-run tools on analyzed files to get fresh remaining issue counts.

    Reuses the original tool execution cwd for path/config consistency.
    Uses a module-level lock around ``os.chdir`` because it is process-global.
    """
    try:
        from lintro.tools import tool_manager
    except ImportError:
        return []

    rerun_results: list[ToolResult] = []
    for tool_name, (result, issues) in by_tool.items():
        file_paths = sorted({issue.file for issue in issues if issue.file})
        if not file_paths:
            continue

        rerun_paths = _paths_for_rerun_context(file_paths=file_paths, cwd=result.cwd)

        try:
            tool = tool_manager.get_tool(tool_name)
            if result.cwd:
                with _rerun_cwd_lock:
                    original_cwd = Path.cwd()
                    os.chdir(result.cwd)
                    try:
                        rerun_results.append(tool.check(rerun_paths, {}))
                    finally:
                        os.chdir(original_cwd)
            else:
                rerun_results.append(tool.check(rerun_paths, {}))
        except Exception:
            loguru_logger.debug(
                f"AI post-fix rerun failed for {tool_name}",
                exc_info=True,
            )
            continue
    return rerun_results
