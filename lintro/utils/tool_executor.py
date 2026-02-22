"""Helper functions for tool execution.

Clean, straightforward approach using Loguru with rich formatting:
1. OutputManager - handles structured output files only
2. ThreadSafeConsoleLogger - handles console display with thread-safe message
   tracking for parallel execution
3. No tee, no stream redirection, no complex state management

Supports parallel execution when enabled via configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.enums.action import Action, normalize_action
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.config import load_post_checks_config
from lintro.utils.execution.exit_codes import (
    DEFAULT_EXIT_CODE_FAILURE,
    DEFAULT_EXIT_CODE_SUCCESS,
    DEFAULT_REMAINING_COUNT,
    aggregate_tool_results,
    determine_exit_code,
)
from lintro.utils.execution.parallel_executor import run_tools_parallel
from lintro.utils.execution.tool_configuration import (
    configure_tool_for_execution,
    get_tool_display_name,
    get_tools_to_run,
)
from lintro.utils.output import OutputManager
from lintro.utils.post_checks import execute_post_checks
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    pass

# Re-export constants for backwards compatibility
__all__ = [
    "DEFAULT_EXIT_CODE_FAILURE",
    "DEFAULT_EXIT_CODE_SUCCESS",
    "DEFAULT_REMAINING_COUNT",
    "run_lint_tools_simple",
]


def run_lint_tools_simple(
    *,
    action: str | Action,
    paths: list[str],
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
    include_venv: bool,
    group_by: str,
    output_format: str,
    verbose: bool,
    raw_output: bool = False,
    output_file: str | None = None,
    incremental: bool = False,
    debug: bool = False,
    stream: bool = False,
    no_log: bool = False,
    auto_install: bool = False,
    yes: bool = False,
) -> int:
    """Simplified runner using Loguru-based logging with rich formatting.

    Clean approach with beautiful output:
    - ThreadSafeConsoleLogger handles ALL console output with thread-safe
      message tracking
    - OutputManager handles structured output files
    - No tee, no complex state management

    Args:
        action: Action to perform ("check", "fmt", "test").
        paths: List of paths to check.
        tools: Comma-separated list of tools to run.
        tool_options: Additional tool options.
        exclude: Patterns to exclude.
        include_venv: Whether to include virtual environments.
        group_by: How to group results.
        output_format: Output format for results.
        verbose: Whether to enable verbose output.
        raw_output: Whether to show raw tool output instead of formatted output.
        output_file: Optional file path to write results to.
        incremental: Whether to only check files changed since last run.
        debug: Whether to show DEBUG messages on console.
        stream: Whether to stream output in real-time (not yet implemented).
        no_log: Whether to disable file logging (not yet implemented).
        auto_install: Whether to auto-install Node.js deps if node_modules missing.
        yes: Skip confirmation prompt and proceed immediately.

    Returns:
        Exit code (0 for success, 1 for failures).

    Raises:
        TypeError: If a programming error occurs during tool execution.
        AttributeError: If a programming error occurs during tool execution.
    """
    # Normalize action to enum
    action = normalize_action(action)

    # Initialize output manager for this run
    output_manager = OutputManager()

    # Initialize Loguru logging (must happen before any logger.debug() calls)
    from lintro.utils.logger_setup import setup_execution_logging

    setup_execution_logging(output_manager.run_dir, debug=debug)

    # Create simplified logger with rich formatting
    from lintro.utils.console import create_logger

    logger = create_logger(run_dir=output_manager.run_dir)

    # Get tools to run (now returns ToolsToRunResult with skip info)
    try:
        tools_result = get_tools_to_run(tools, action)
    except ValueError as e:
        logger.console_output(f"Error: {e}")
        return 1

    tools_to_run = tools_result.to_run
    skipped_tools = tools_result.skipped

    if not tools_to_run and not skipped_tools:
        logger.console_output("No tools to run.")
        return 0

    if not tools_to_run and skipped_tools:
        skipped_names = ", ".join(st.name for st in skipped_tools)
        logger.console_output(
            f"All tools were skipped ({len(skipped_tools)}): {skipped_names}",
        )

    # Load post-checks config early to exclude those tools from main phase
    post_cfg_early = load_post_checks_config()
    post_enabled_early = bool(post_cfg_early.get("enabled", False))
    post_tools_early: set[str] = (
        {t.lower() for t in (post_cfg_early.get("tools", []) or [])}
        if post_enabled_early
        else set()
    )

    # Filter out post-check tools from main phase
    if post_tools_early:
        tools_to_run = [t for t in tools_to_run if t.lower() not in post_tools_early]

    # If early post-check filtering removed all tools from the main phase,
    # that's okay - post-checks will still run. Just log the situation.
    # Track this state so we can return failure if post-checks don't run.
    main_phase_empty_due_to_filter = bool(not tools_to_run and post_tools_early)
    if main_phase_empty_due_to_filter:
        logger.console_output(
            text=(
                "All selected tools are configured as post-checks - "
                "skipping main phase"
            ),
        )

    # Print main header with output directory information
    logger.print_lintro_header()

    # Show incremental mode message
    if incremental:
        logger.console_output(
            text="Incremental mode: only checking files changed since last run",
            color="cyan",
        )

    # Execute tools and collect results
    all_results: list[ToolResult] = []
    total_issues = 0
    total_fixed = 0
    total_remaining = 0

    # Parse tool options once for all tools
    from lintro.utils.tool_options import parse_tool_options

    tool_option_dict = parse_tool_options(tool_options)

    # Create UnifiedConfigManager once before the loop
    config_manager = UnifiedConfigManager()

    # Check if parallel execution is enabled
    from lintro.config.config_loader import get_config

    lintro_config = get_config()
    use_parallel = lintro_config.execution.parallel and len(tools_to_run) > 1

    # Determine auto_install: CLI flag > config > container default
    from lintro.utils.environment.container_detection import is_container_environment

    is_container = is_container_environment()
    if auto_install:
        effective_auto_install = True
    elif lintro_config.execution.auto_install_deps is not None:
        effective_auto_install = lintro_config.execution.auto_install_deps
    else:
        effective_auto_install = is_container

    # Pre-execution config summary (suppress in JSON mode)
    if output_format.lower() != "json" and (tools_to_run or skipped_tools):
        from lintro.utils.console.pre_execution_summary import (
            print_pre_execution_summary,
        )
        from lintro.utils.environment import detect_ci_environment

        # Collect per-tool auto_install settings
        per_tool_auto: dict[str, bool | None] = {}
        for name in tools_to_run:
            tool_cfg = lintro_config.get_tool_config(name)
            if tool_cfg.auto_install is not None:
                per_tool_auto[name] = tool_cfg.auto_install

        ci_env = detect_ci_environment()
        is_ci = ci_env is not None and ci_env.is_ci
        print_pre_execution_summary(
            tools_to_run=tools_to_run,
            skipped_tools=skipped_tools,
            effective_auto_install=effective_auto_install,
            is_container=is_container,
            is_ci=is_ci,
            per_tool_auto_install=per_tool_auto if per_tool_auto else None,
        )

        # Confirmation prompt — skip when non-interactive
        import sys

        auto_continue = yes or is_ci or not sys.stdin.isatty()
        if not auto_continue:
            try:
                answer = input("Proceed? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer in ("n", "no"):
                logger.console_output(text="Aborted.", color="yellow")
                return int(DEFAULT_EXIT_CODE_SUCCESS)

    # Define success_func once before the loop
    def success_func(message: str) -> None:
        logger.console_output(text=message, color="green")

    # Use parallel execution if enabled
    if use_parallel:
        logger.console_output(
            text=f"Running {len(tools_to_run)} tools in parallel "
            f"(max {lintro_config.execution.max_workers} workers)",
        )
        all_results = run_tools_parallel(
            tools_to_run=tools_to_run,
            paths=paths,
            action=action,
            config_manager=config_manager,
            tool_option_dict=tool_option_dict,
            exclude=exclude,
            include_venv=include_venv,
            post_tools=post_tools_early,
            max_workers=lintro_config.execution.max_workers,
            incremental=incremental,
            auto_install=effective_auto_install,
        )

        # Calculate totals from parallel results using helper
        total_issues, total_fixed, total_remaining = aggregate_tool_results(
            all_results,
            action,
        )
        # Display results for parallel execution
        for result in all_results:
            # Print tool header like sequential mode does
            display_name = get_tool_display_name(result.name)
            logger.print_tool_header(tool_name=display_name, action=action)

            display_output: str | None = None
            if result.formatted_output:
                display_output = result.formatted_output
            elif result.issues or result.output:
                from lintro.utils.output import format_tool_output

                display_output = format_tool_output(
                    tool_name=result.name,
                    output=result.output or "",
                    output_format=output_format,
                    issues=list(result.issues) if result.issues else None,
                )
            if result.output and raw_output:
                display_output = result.output

            if display_output and display_output.strip():
                from lintro.utils.result_formatters import print_tool_result

                print_tool_result(
                    console_output_func=logger.console_output,
                    success_func=success_func,
                    tool_name=result.name,
                    output=display_output,
                    issues_count=result.issues_count,
                    raw_output_for_meta=result.output,
                    action=action,
                    success=result.success,
                )
            elif result.issues_count == 0 and result.success:
                logger.console_output(
                    text="✓ No issues found.",
                    color="green",
                )

    else:
        # Sequential execution (original behavior)
        for tool_name in tools_to_run:
            try:
                tool = tool_manager.get_tool(tool_name)
                display_name = get_tool_display_name(tool_name)

                # Print tool header before execution
                logger.print_tool_header(tool_name=display_name, action=action)

                # Configure tool using shared helper
                configure_tool_for_execution(
                    tool=tool,
                    tool_name=tool_name,
                    config_manager=config_manager,
                    tool_option_dict=tool_option_dict,
                    exclude=exclude,
                    include_venv=include_venv,
                    incremental=incremental,
                    action=action,
                    post_tools=post_tools_early,
                    auto_install=effective_auto_install,
                    lintro_config=lintro_config,
                )

                # Execute the tool
                result = (
                    tool.fix(paths, {})
                    if action == Action.FIX
                    else tool.check(paths, {})
                )

                all_results.append(result)

                # Update totals
                total_issues += getattr(result, "issues_count", 0)
                if action == Action.FIX:
                    fixed_count = getattr(result, "fixed_issues_count", None)
                    total_fixed += fixed_count if fixed_count is not None else 0
                    remaining_count = getattr(result, "remaining_issues_count", None)
                    total_remaining += (
                        remaining_count if remaining_count is not None else 0
                    )

                # Use formatted_output if available, otherwise format from issues
                display_output = None
                if result.formatted_output:
                    display_output = result.formatted_output
                elif (
                    action == Action.FIX
                    and result.detected_issues
                ):
                    # Fix mode with detected issues: render two-table output
                    from lintro.formatters.formatter import format_fix_results

                    display_output = format_fix_results(
                        detected_issues=result.detected_issues,
                        remaining_issues=(
                            list(result.issues) if result.issues else None
                        ),
                        output_format=output_format,
                        tool_name=tool_name,
                    )
                elif result.issues or result.output:
                    # Format issues using the tool formatter
                    # Also format when there's output (e.g., coverage) even with no
                    # issues
                    from lintro.utils.output import format_tool_output

                    display_output = format_tool_output(
                        tool_name=tool_name,
                        output=result.output or "",
                        output_format=output_format,
                        issues=list(result.issues) if result.issues else None,
                    )
                if result.output and raw_output:
                    # Use raw output when raw_output flag is True (overrides formatted)
                    display_output = result.output

                # Display the formatted output if available
                if display_output and display_output.strip():
                    from lintro.utils.result_formatters import print_tool_result

                    print_tool_result(
                        console_output_func=logger.console_output,
                        success_func=success_func,
                        tool_name=tool_name,
                        output=display_output,
                        issues_count=result.issues_count,
                        raw_output_for_meta=result.output,
                        action=action,
                        success=result.success,
                    )
                elif result.issues_count == 0 and result.success:
                    # Show success message when no issues found and no output
                    logger.console_output(text="Processing files")
                    logger.console_output(text="✓ No issues found.", color="green")
                    logger.console_output(text="")

            except (TypeError, AttributeError):
                # Programming errors should be re-raised for debugging
                from loguru import logger as loguru_logger

                loguru_logger.exception(f"Programming error running {tool_name}")
                raise
            except (OSError, ValueError, RuntimeError) as e:
                from loguru import logger as loguru_logger

                # Log full exception with traceback to debug.log via loguru
                loguru_logger.exception(f"Error running {tool_name}")
                # Show user-friendly error message on console
                logger.console_output(f"Error running {tool_name}: {e}")

                # Create a failed result for this tool
                failed_result = ToolResult(
                    name=tool_name,
                    success=False,
                    output=f"Failed to initialize tool: {e}",
                    issues_count=0,
                )
                all_results.append(failed_result)

    # Add skipped tool results for display in summary table
    for st in skipped_tools:
        all_results.append(
            ToolResult(
                name=st.name,
                skipped=True,
                skip_reason=st.reason,
                issues_count=0,
            ),
        )

    # Execute post-checks if configured
    total_issues, total_fixed, total_remaining = execute_post_checks(
        action=action,
        paths=paths,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        logger=logger,
        all_results=all_results,
        total_issues=total_issues,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
    )

    # Determine final exit code once — used for both JSON output and return
    final_exit_code = int(
        determine_exit_code(
            action=action,
            all_results=all_results,
            total_issues=total_issues,
            total_remaining=total_remaining,
            main_phase_empty_due_to_filter=main_phase_empty_due_to_filter,
        ),
    )

    # Display results
    if all_results:
        if output_format.lower() == "json":
            # Output JSON to stdout
            import json

            from lintro.utils.json_output import create_json_output

            json_data = create_json_output(
                action=str(action),
                results=all_results,
                total_issues=total_issues,
                total_fixed=total_fixed,
                total_remaining=total_remaining,
                exit_code=final_exit_code,
            )
            print(json.dumps(json_data, indent=2))
        else:
            logger.print_execution_summary(action, all_results)

        # Write report files (markdown, html, csv)
        try:
            output_manager.write_reports_from_results(all_results)
        except (OSError, ValueError, TypeError) as e:
            logger.console_output(f"Warning: Failed to write reports: {e}")
            # Continue execution - report writing failures should not stop the tool

    return final_exit_code
