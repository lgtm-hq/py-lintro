"""Post-check execution utilities.

Handles optional post-check tools that run after primary linting tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action
from lintro.enums.group_by import normalize_group_by
from lintro.enums.output_format import OutputFormat, normalize_output_format
from lintro.plugins.registry import ToolRegistry
from lintro.tools import tool_manager
from lintro.utils.config import load_module_size_config, load_post_checks_config
from lintro.utils.module_size import (
    find_oversized_modules,
    resolve_module_size_settings,
)
from lintro.utils.output import format_tool_output
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.console import ThreadSafeConsoleLogger


def _run_module_size_gate(
    *,
    paths: list[str],
    exclude: str | None,
    include_venv: bool,
    json_output_mode: bool,
    logger: ThreadSafeConsoleLogger,
) -> None:
    """Run the warn-level module-size gate over the scanned paths.

    This gate never fails the build. It emits a warning for any Python module
    that exceeds the configured threshold and is not baselined. See
    ``lintro/utils/module_size.py`` for the ratchet-down plan.

    Args:
        paths: Paths being linted.
        exclude: Additional comma-separated exclude patterns from the CLI.
        include_venv: Whether virtual-environment directories are included.
        json_output_mode: Whether output is JSON (suppresses console warnings).
        logger: Logger instance for console output.
    """
    cfg = load_module_size_config()
    if not bool(cfg.get("enabled", True)):
        return

    threshold, baseline, base_excludes = resolve_module_size_settings(config=cfg)
    exclude_patterns: list[str] = list(base_excludes)
    if exclude:
        exclude_patterns.extend(p.strip() for p in exclude.split(",") if p.strip())

    violations = find_oversized_modules(
        paths=paths,
        threshold=threshold,
        baseline=baseline,
        exclude_patterns=tuple(exclude_patterns),
        include_venv=include_venv,
    )

    if not violations or json_output_mode:
        return

    logger.console_output(
        text=(
            f"Warning: {len(violations)} module(s) exceed the "
            f"{threshold}-line size limit (warn-level, non-blocking):"
        ),
        color="yellow",
    )
    for module in violations:
        logger.console_output(
            text=f"  {module.path} ({module.line_count} lines)",
            color="yellow",
        )
    logger.console_output(text="")


def execute_post_checks(
    *,
    action: Action,
    paths: list[str],
    exclude: str | None,
    include_venv: bool,
    group_by: str,
    output_format: str,
    verbose: bool,
    raw_output: bool,
    logger: ThreadSafeConsoleLogger,
    all_results: list[ToolResult],
    total_issues: int,
    total_fixed: int,
    total_remaining: int,
    diff_base: str | None = None,
) -> tuple[int, int, int]:
    """Execute post-check tools after primary linting.

    Args:
        action: The action being performed.
        paths: List of paths to check.
        exclude: Patterns to exclude.
        include_venv: Whether to include virtual environments.
        group_by: How to group results.
        output_format: Output format for results.
        verbose: Whether to enable verbose output.
        raw_output: Whether to show raw tool output.
        logger: Logger instance for output.
        all_results: List to append results to.
        total_issues: Current total issues count.
        total_fixed: Current total fixed count.
        total_remaining: Current total remaining count.
        diff_base: Optional git ref to scope post-checks to changed files.

    Returns:
        tuple[int, int, int]: Updated (total_issues, total_fixed, total_remaining)
    """
    # Skip post-checks for test action - test is independent from linting/formatting
    if action == Action.TEST:
        return (total_issues, total_fixed, total_remaining)

    # Normalize enums while maintaining backward compatibility
    output_fmt_enum: OutputFormat = normalize_output_format(output_format)
    _ = normalize_group_by(group_by)  # Normalize for validation, return value unused
    json_output_mode = output_fmt_enum == OutputFormat.JSON

    # Load post-checks config
    post_cfg = load_post_checks_config()
    post_enabled = bool(post_cfg.get("enabled", False))
    post_tools = list(post_cfg.get("tools", [])) if post_enabled else []
    enforce_failure = bool(post_cfg.get("enforce_failure", action == Action.CHECK))

    # In JSON mode, we still need exit-code enforcement even if we skip
    # rendering post-check outputs. If a post-check tool is unavailable
    # and enforce_failure is enabled during check, append a failure result
    # so summaries and exit codes reflect the condition.
    if post_tools and json_output_mode and action == Action.CHECK and enforce_failure:
        for post_tool_name in post_tools:
            tool_name_lower = post_tool_name.lower()
            if not ToolRegistry.is_registered(tool_name_lower):
                from lintro.models.core.tool_result import ToolResult

                all_results.append(
                    ToolResult(
                        name=post_tool_name,
                        success=False,
                        output=f"Tool '{post_tool_name}' not registered",
                        issues_count=1,
                    ),
                )

    if post_tools:
        # Print a clear post-checks section header (only when not in JSON mode)
        if not json_output_mode:
            logger.print_post_checks_header()

        for post_tool_name in post_tools:
            tool_name_lower = post_tool_name.lower()

            if not ToolRegistry.is_registered(tool_name_lower):
                logger.console_output(
                    text=f"Warning: Unknown post-check tool: {post_tool_name}",
                    color="yellow",
                )
                continue

            # If the tool isn't available in the current environment (e.g., unit
            # tests that stub a limited set of tools), skip without enforcing
            # failure. Post-checks are optional when the tool cannot be resolved
            # from the tool manager.
            try:
                tool = tool_manager.get_tool(tool_name_lower)
            except (KeyError, ValueError, RuntimeError) as e:
                logger.console_output(
                    text=f"Warning: Post-check '{post_tool_name}' unavailable: {e}",
                    color="yellow",
                )
                continue

            # Operate on an isolated per-invocation copy so concurrent runs
            # never mutate the shared registry singleton's option state. The
            # singleton fetched above is used only as a lookup template; all
            # configuration below (apply_config_to_tool, set_options) and the
            # subsequent execution target this private copy. Mirrors the
            # isolation performed by configure_tool_for_execution() on the
            # primary sequential and parallel execution paths.
            tool = tool.copy_for_execution()
            tool.reset_options()

            # Post-checks run with explicit headers (reuse standard header)
            if not json_output_mode:
                logger.print_tool_header(tool_name=tool_name_lower, action=action)

            try:
                # Configure post-check tool using UnifiedConfigManager
                # This replaces manual sync logic with unified config management
                post_config_manager = UnifiedConfigManager()
                post_config_manager.apply_config_to_tool(tool=tool)

                tool.set_options(include_venv=include_venv)
                if exclude:
                    exclude_patterns: list[str] = [
                        p.strip() for p in exclude.split(",")
                    ]
                    tool.set_options(exclude_patterns=exclude_patterns)
                if diff_base is not None:
                    tool.set_options(diff_base=diff_base)

                # For check: Black should run in check mode; for fmt: run fix
                if action == Action.FIX and tool.definition.can_fix:
                    result = tool.fix(paths=paths, options={})
                    issues_count = getattr(result, "issues_count", 0)
                    fixed_count = getattr(result, "fixed_issues_count", None)
                    total_fixed += fixed_count if fixed_count is not None else 0
                    remaining_count = getattr(result, "remaining_issues_count", None)
                    total_remaining += (
                        remaining_count if remaining_count is not None else issues_count
                    )
                else:
                    result = tool.check(paths=paths, options={})
                    issues_count = getattr(result, "issues_count", 0)
                    total_issues += issues_count

                # Format and display output
                output = getattr(result, "output", None)
                issues = getattr(result, "issues", None)
                formatted_output: str = ""
                if (output and output.strip()) or issues:
                    formatted_output = format_tool_output(
                        tool_name=tool_name_lower,
                        output=output or "",
                        output_format=output_fmt_enum.value,
                        issues=issues,
                        success=getattr(result, "success", None),
                        issues_count=issues_count,
                    )

                if not json_output_mode:
                    from lintro.utils.result_formatters import print_tool_result

                    def success_func(message: str) -> None:
                        logger.console_output(text=message, color="green")

                    if formatted_output and formatted_output.strip():
                        print_tool_result(
                            console_output_func=logger.console_output,
                            success_func=success_func,
                            tool_name=tool_name_lower,
                            output=(
                                formatted_output if not raw_output else (output or "")
                            ),
                            issues_count=issues_count,
                            raw_output_for_meta=output,
                            action=action,
                            success=getattr(result, "success", None),
                        )
                    elif issues_count == 0 and getattr(result, "success", True):
                        # Show success message when no issues found
                        logger.console_output(text="Processing files")
                        logger.console_output(text="✓ No issues found.", color="green")
                        logger.console_output(text="")

                all_results.append(result)
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as e:
                # Do not crash the entire run due to missing optional post-check
                # tool
                logger.console_output(
                    text=f"Warning: Post-check '{post_tool_name}' failed: {e}",
                    color="yellow",
                )
                # Only enforce failure when the tool was available and executed
                if enforce_failure and action == Action.CHECK:
                    from lintro.models.core.tool_result import ToolResult

                    all_results.append(
                        ToolResult(
                            name=post_tool_name,
                            success=False,
                            output=str(e),
                            issues_count=1,
                        ),
                    )

    # Warn-level module-size gate (issue #1052). Runs independently of the
    # configured post-check tools and never contributes to failure counts.
    _run_module_size_gate(
        paths=paths,
        exclude=exclude,
        include_venv=include_venv,
        json_output_mode=json_output_mode,
        logger=logger,
    )

    return total_issues, total_fixed, total_remaining
