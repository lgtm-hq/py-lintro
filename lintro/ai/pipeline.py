"""AI fix pipeline: generate, classify, apply, validate, post-summary."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from lintro.ai.display import render_summary, render_validation
from lintro.ai.fix import generate_fixes
from lintro.ai.interactive import apply_fixes, review_fixes_interactive
from lintro.ai.metadata import (
    attach_fix_suggestions_metadata,
    attach_fixed_count_metadata,
    attach_validation_counts_metadata,
)
from lintro.ai.rerun import apply_rerun_results, rerun_tools
from lintro.ai.risk import is_safe_style_fix
from lintro.ai.summary import generate_post_fix_summary
from lintro.ai.validation import validate_applied_fixes

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.models import AIFixSuggestion
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue
    from lintro.utils.console.logger import ThreadSafeConsoleLogger


def run_fix_pipeline(
    *,
    fix_issues: list[tuple[ToolResult, BaseIssue]],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    output_format: str,
    workspace_root: Path,
) -> None:
    """Generate and optionally apply AI fix suggestions across all tools.

    This is the main fix pipeline that:
    1. Groups issues by tool and generates fix suggestions
    2. Classifies fixes as safe-style or behavioral-risk
    3. Auto-applies safe fixes in non-interactive mode
    4. Presents risky fixes for interactive review
    5. Re-runs tools to verify fixes
    6. Generates post-fix summary

    Args:
        fix_issues: List of (tool_result, issue) pairs to fix.
        provider: AI provider instance.
        ai_config: AI configuration.
        logger: Console logger for output.
        output_format: Output format string.
        workspace_root: Workspace root path.
    """
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

        if not issues:
            continue

        loguru_logger.debug(
            f"AI fix: {tool_name} has {len(issues)} issues, "
            f"budget={remaining_budget}",
        )

        suggestions = generate_fixes(
            issues,
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
        remaining_budget -= len(issues[:remaining_budget])
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
            auto_apply=True,
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
            auto_apply=True,
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
        fresh_remaining_results = rerun_tools(by_tool)
        if fresh_remaining_results:
            apply_rerun_results(
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
