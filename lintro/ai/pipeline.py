"""AI fix pipeline: generate, classify, apply, validate, post-summary."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from lintro.ai.apply import apply_fixes
from lintro.ai.audit import write_audit_log
from lintro.ai.budget import CostBudget
from lintro.ai.display import render_fixes, render_summary, render_validation
from lintro.ai.enums import ConfidenceLevel
from lintro.ai.fix import generate_fixes
from lintro.ai.fix_params import FixGenParams
from lintro.ai.interactive import review_fixes_interactive
from lintro.ai.metadata import (
    attach_fix_suggestions_metadata,
    attach_fixed_count_metadata,
    attach_telemetry_metadata,
    attach_validation_counts_metadata,
)
from lintro.ai.refinement import refine_unverified_fixes
from lintro.ai.risk import is_safe_style_fix
from lintro.ai.summary import generate_post_fix_summary
from lintro.ai.telemetry import AITelemetry
from lintro.ai.undo import save_undo_patch
from lintro.ai.validation import ValidationResult, validate_applied_fixes, verify_fixes
from lintro.enums.output_format import OutputFormat

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.models import AIFixSuggestion
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue
    from lintro.utils.console.logger import ThreadSafeConsoleLogger


def _confidence_numeric(value: ConfidenceLevel | str) -> int:
    """Return numeric ordering for a confidence value (3=high, 2=medium, 1=low)."""
    try:
        return ConfidenceLevel(value).numeric_order
    except ValueError:
        return 1


def _generate_all_suggestions(
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    workspace_root: Path,
    is_json: bool,
    telemetry: AITelemetry,
    budget: CostBudget | None,
) -> list[AIFixSuggestion]:
    """Iterate tools, generate fix suggestions, and collect telemetry."""
    all_suggestions: list[AIFixSuggestion] = []
    remaining_budget = ai_config.max_fix_issues

    total_fix_issues = sum(len(issues) for _, issues in by_tool.values())
    if not is_json and total_fix_issues > 0:
        logger.console_output(
            f"  AI: generating fixes for {total_fix_issues} issues...",
        )

    def _progress_callback(completed: int, total: int) -> None:
        if not is_json:
            logger.console_output(
                f"  AI: generating fixes... {completed}/{total}",
            )

    for tool_name, (result, issues) in by_tool.items():
        if remaining_budget <= 0:
            break
        if not issues:
            continue
        if budget is not None:
            budget.check()

        loguru_logger.debug(
            f"AI fix: {tool_name} has {len(issues)} issues, "
            f"budget={remaining_budget}",
        )

        fix_params = FixGenParams(
            tool_name=tool_name,
            workspace_root=workspace_root,
            max_issues=remaining_budget,
            max_workers=ai_config.max_parallel_calls,
            max_tokens=ai_config.max_tokens,
            max_retries=ai_config.max_retries,
            timeout=ai_config.api_timeout,
            context_lines=ai_config.context_lines,
            base_delay=ai_config.retry_base_delay,
            max_delay=ai_config.retry_max_delay,
            backoff_factor=ai_config.retry_backoff_factor,
            max_prompt_tokens=ai_config.max_prompt_tokens,
            enable_cache=ai_config.enable_cache,
            cache_ttl=ai_config.cache_ttl,
            progress_callback=_progress_callback,
            fallback_models=ai_config.fallback_models,
            sanitize_mode=ai_config.sanitize_mode,
        )
        suggestions = generate_fixes(
            issues,
            provider,
            tool_name=fix_params.tool_name,
            max_issues=fix_params.max_issues,
            max_workers=fix_params.max_workers,
            workspace_root=fix_params.workspace_root,
            max_tokens=fix_params.max_tokens,
            max_retries=fix_params.max_retries,
            timeout=fix_params.timeout,
            context_lines=fix_params.context_lines,
            base_delay=fix_params.base_delay,
            max_delay=fix_params.max_delay,
            backoff_factor=fix_params.backoff_factor,
            max_prompt_tokens=fix_params.max_prompt_tokens,
            enable_cache=fix_params.enable_cache,
            cache_ttl=fix_params.cache_ttl,
            progress_callback=fix_params.progress_callback,
            fallback_models=fix_params.fallback_models,
            sanitize_mode=fix_params.sanitize_mode,
        )
        for suggestion in suggestions:
            if not suggestion.tool_name:
                suggestion.tool_name = tool_name
        remaining_budget -= len(issues[:remaining_budget])

        if ai_config.verbose:
            loguru_logger.info(
                f"AI fix: {tool_name} generated {len(suggestions)} suggestions",
            )

        telemetry.total_api_calls += len(suggestions)
        for s in suggestions:
            telemetry.total_input_tokens += s.input_tokens
            telemetry.total_output_tokens += s.output_tokens
            telemetry.total_cost_usd += s.cost_estimate

        if budget is not None:
            budget.record(sum(s.cost_estimate for s in suggestions))

        if ai_config.verbose:
            tool_cost = sum(s.cost_estimate for s in suggestions)
            loguru_logger.info(
                f"AI fix: {tool_name} cost=${tool_cost:.6f}, "
                f"cumulative=${telemetry.total_cost_usd:.6f}",
            )

        all_suggestions.extend(suggestions)

        if suggestions:
            attach_fix_suggestions_metadata(result, suggestions)

    return all_suggestions


def _filter_by_confidence(
    all_suggestions: list[AIFixSuggestion],
    ai_config: AIConfig,
) -> list[AIFixSuggestion]:
    """Apply confidence threshold filter and log diagnostics."""
    threshold = _confidence_numeric(ai_config.min_confidence)
    filtered = [
        s for s in all_suggestions if _confidence_numeric(s.confidence) >= threshold
    ]

    if ai_config.verbose and filtered:
        confidence_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        for s in filtered:
            confidence_counts[s.confidence] = confidence_counts.get(s.confidence, 0) + 1
            risk_label = s.risk_level or "unclassified"
            risk_counts[risk_label] = risk_counts.get(risk_label, 0) + 1
        loguru_logger.info(
            f"AI fix: confidence breakdown: {confidence_counts}",
        )
        loguru_logger.info(
            f"AI fix: risk breakdown: {risk_counts}",
        )

    return filtered


def _apply_or_review(
    all_suggestions: list[AIFixSuggestion],
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    is_json: bool,
    workspace_root: Path,
) -> tuple[int, int, list[AIFixSuggestion]]:
    """Auto-apply safe fixes, then auto-apply or interactively review the rest.

    Returns (applied, rejected, applied_suggestions).
    """
    applied = 0
    rejected = 0
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
        save_undo_patch(safe_suggestions, workspace_root)
        applied_safe = apply_fixes(
            safe_suggestions,
            workspace_root=workspace_root,
            auto_apply=True,
            search_radius=ai_config.fix_search_radius,
        )
        applied_suggestions.extend(applied_safe)
        applied += len(applied_safe)
        safe_failed = len(safe_suggestions) - len(applied_safe)
        if not is_json:
            msg = (
                f"  AI: auto-applied {len(applied_safe)}/{len(safe_suggestions)}"
                f" safe-style fixes"
            )
            if safe_failed:
                msg += f" ({safe_failed} failed)"
            logger.console_output(msg)

    if ai_config.auto_apply:
        auto_apply_candidates = (
            risky_suggestions if safe_fast_path_applied else all_suggestions
        )
        save_undo_patch(auto_apply_candidates, workspace_root)
        auto_applied = apply_fixes(
            auto_apply_candidates,
            workspace_root=workspace_root,
            auto_apply=True,
            search_radius=ai_config.fix_search_radius,
        )
        applied_suggestions.extend(auto_applied)
        applied += len(auto_applied)
        rejected = len(auto_apply_candidates) - len(auto_applied) + safe_failed
        if not is_json:
            logger.console_output(
                f"  AI: auto-applied {applied}/{len(all_suggestions)} fixes",
            )
    elif not is_json:
        review_candidates = (
            risky_suggestions if safe_fast_path_applied else all_suggestions
        )
        save_undo_patch(review_candidates, workspace_root)
        accepted_count, rejected_count, interactive_applied = review_fixes_interactive(
            review_candidates,
            validate_after_group=ai_config.validate_after_group,
            workspace_root=workspace_root,
            search_radius=ai_config.fix_search_radius,
        )
        applied += accepted_count
        rejected += rejected_count + safe_failed
        applied_suggestions.extend(interactive_applied)

    return applied, rejected, applied_suggestions


def _verify_and_refine(
    applied_suggestions: list[AIFixSuggestion],
    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    is_json: bool,
    workspace_root: Path,
    telemetry: AITelemetry,
    budget: CostBudget | None,
) -> ValidationResult | None:
    """Verify applied fixes and attempt refinement for unverified ones.

    Returns the ValidationResult (or None).
    """
    validation = verify_fixes(
        applied_suggestions=applied_suggestions,
        by_tool=by_tool,
    )
    if not is_json and validation and (validation.verified or validation.unverified):
        val_output = render_validation(validation)
        if val_output:
            logger.console_output(val_output)

    if (
        validation
        and validation.unverified > 0
        and ai_config.max_refinement_attempts >= 1
    ):
        refined, refinement_cost = refine_unverified_fixes(
            applied_suggestions=applied_suggestions,
            validation=validation,
            provider=provider,
            ai_config=ai_config,
            workspace_root=workspace_root,
        )
        if refined:
            telemetry.total_api_calls += len(refined)
            for s in refined:
                telemetry.total_input_tokens += s.input_tokens
                telemetry.total_output_tokens += s.output_tokens
                telemetry.total_cost_usd += s.cost_estimate
            if budget is not None:
                budget.record(refinement_cost)

            re_validation = validate_applied_fixes(refined)
            if re_validation:
                validation.verified += re_validation.verified
                validation.unverified -= re_validation.verified
                # Merge per-tool counters from refinement re-validation
                for tool, count in re_validation.verified_by_tool.items():
                    validation.verified_by_tool[tool] = (
                        validation.verified_by_tool.get(tool, 0) + count
                    )
                    # Decrease unverified for the same tool accordingly
                    if tool in validation.unverified_by_tool:
                        validation.unverified_by_tool[tool] = max(
                            0,
                            validation.unverified_by_tool[tool] - count,
                        )
                if not is_json and re_validation.verified:
                    logger.console_output(
                        f"  AI: refinement verified "
                        f"{re_validation.verified} additional fix(es)",
                    )
            if ai_config.verbose:
                loguru_logger.info(
                    f"AI fix: refinement cost=${refinement_cost:.6f}",
                )

    return validation


def run_fix_pipeline(
    *,
    fix_issues: list[tuple[ToolResult, BaseIssue]],
    provider: BaseAIProvider,
    ai_config: AIConfig,
    logger: ThreadSafeConsoleLogger,
    output_format: str,
    workspace_root: Path,
    budget: CostBudget | None = None,
) -> tuple[int, int, list[AIFixSuggestion]]:
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
        budget: Optional cost budget tracker.

    Returns:
        Tuple of (fixes_applied, fixes_failed).
    """
    telemetry = AITelemetry()

    by_tool: dict[str, tuple[ToolResult, list[BaseIssue]]] = {}
    for result, issue in fix_issues:
        if result.name not in by_tool:
            by_tool[result.name] = (result, [])
        by_tool[result.name][1].append(issue)

    is_json = output_format.lower() == OutputFormat.JSON

    # Step 1: Generate suggestions across all tools
    all_suggestions = _generate_all_suggestions(
        by_tool,
        provider,
        ai_config,
        logger,
        workspace_root,
        is_json,
        telemetry,
        budget,
    )

    # Step 2: Apply confidence threshold filter
    total_generated = len(all_suggestions)
    all_suggestions = _filter_by_confidence(all_suggestions, ai_config)
    filtered_out = total_generated - len(all_suggestions)

    if not all_suggestions:
        telemetry.successful_fixes = 0
        telemetry.failed_fixes = total_generated
        write_audit_log(workspace_root, [], 0, telemetry.total_cost_usd)
        attach_telemetry_metadata(
            [r for r, _ in by_tool.values()],
            telemetry,
        )
        return (0, 0, [])

    # Dry-run mode: display fixes but do not apply them
    if ai_config.dry_run:
        if not is_json:
            rendered = render_fixes(
                all_suggestions,
                show_cost=ai_config.show_cost_estimate,
            )
            if rendered:
                logger.console_output(rendered)
            logger.console_output(
                "  AI: dry-run mode — fixes displayed but not applied",
            )
        return (0, 0, all_suggestions)

    # Step 3: Apply or review fixes
    applied, rejected, applied_suggestions = _apply_or_review(
        all_suggestions,
        ai_config,
        logger,
        is_json,
        workspace_root,
    )

    telemetry.successful_fixes = applied
    telemetry.failed_fixes = (len(all_suggestions) - applied) + filtered_out

    # Step 4: Verify and refine applied fixes
    validation = None
    if applied_suggestions:
        validation = _verify_and_refine(
            applied_suggestions,
            by_tool,
            provider,
            ai_config,
            logger,
            is_json,
            workspace_root,
            telemetry,
            budget,
        )

    # Attach metadata to tool results
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

    # Generate post-fix summary — only when validation confirms outcomes
    if (applied > 0 or rejected > 0) and not is_json and validation is not None:
        applied_for_summary = applied
        if validation.verified or validation.unverified:
            applied_for_summary = validation.verified

        # When fixes were applied, by_tool already holds de-duplicated
        # per-tool results (one ToolResult per tool).  When no fixes were
        # applied, we derive unique results from the original fix_issues
        # because by_tool may be empty or stale.
        if applied_suggestions:
            unique_results: list[ToolResult] | None = [
                result for result, _ in by_tool.values()
            ]
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
                timeout=ai_config.api_timeout,
                max_retries=ai_config.max_retries,
                base_delay=ai_config.retry_base_delay,
                max_delay=ai_config.retry_max_delay,
                backoff_factor=ai_config.retry_backoff_factor,
                fallback_models=ai_config.fallback_models,
            )
            if post_summary:
                output = render_summary(
                    post_summary,
                    show_cost=ai_config.show_cost_estimate,
                )
                if output:
                    logger.console_output(output)

    write_audit_log(
        workspace_root,
        applied_suggestions,
        rejected,
        telemetry.total_cost_usd,
    )
    attach_telemetry_metadata(
        [r for r, _ in by_tool.values()],
        telemetry,
    )

    return (applied, len(all_suggestions) - applied, applied_suggestions)


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
