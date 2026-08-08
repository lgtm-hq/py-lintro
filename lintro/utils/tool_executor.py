"""Execute phase of a Lintro run.

The runner is split into two phases (issue #1823):

1. :func:`execute_run` selects tools, runs them, aggregates their results,
   scores the run, and resolves the exit code. It writes no files, emits no
   output document, and imports nothing from :mod:`lintro.ai`. Its product is
   a :class:`~lintro.models.core.run_artifact.RunArtifact`.
2. :func:`lintro.utils.execution.run_renderer.render_run` turns that artifact
   into console/JSON/SARIF/CSV output and the run's files.

:func:`run_lint_tools_simple` remains as a thin, AI-free wrapper over both, so
every existing caller keeps its exit-code contract. Callers that want AI
enhancement run it between the two phases; see
:func:`lintro.ai.interface.enhance_artifact`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from lintro.enums.action import Action, normalize_action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.config import load_post_checks_config
from lintro.utils.execution.exit_codes import (
    DEFAULT_EXIT_CODE_FAILURE,
    DEFAULT_EXIT_CODE_SUCCESS,
    DEFAULT_REMAINING_COUNT,
    aggregate_tool_results,
)

# Aliased to its historical private name: external callers and tests have
# imported ``_run_fix_with_retry`` from this module since before the split.
from lintro.utils.execution.fix_retry import (
    run_fix_with_retry as _run_fix_with_retry,
)
from lintro.utils.execution.parallel_executor import run_tools_parallel
from lintro.utils.execution.result_shaping import (
    enrich_issues_with_doc_urls as _enrich_issues_with_doc_urls,
)
from lintro.utils.execution.result_shaping import (
    filter_result_to_fixable as _filter_result_to_fixable,
)
from lintro.utils.execution.run_aggregation import (
    finalize_artifact,
    refresh_artifact,
    sequential_totals,
)
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.run_preflight import (
    confirm_pre_execution,
    report_skipped_tools,
    resolve_diff_scope,
)
from lintro.utils.execution.run_renderer import _write_artifacts, render_run
from lintro.utils.execution.tool_configuration import (
    configure_tool_for_execution,
    get_tool_display_name,
    get_tools_to_run,
)
from lintro.utils.output import OutputManager
from lintro.utils.post_checks import execute_post_checks
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from collections.abc import Callable

# Re-export constants and internals for backwards compatibility. The private
# names stay importable from here because they were part of this module before
# the execute/render split (issue #1823).
__all__ = [
    "DEFAULT_EXIT_CODE_FAILURE",
    "DEFAULT_EXIT_CODE_SUCCESS",
    "DEFAULT_REMAINING_COUNT",
    "_enrich_issues_with_doc_urls",
    "_filter_result_to_fixable",
    "_run_fix_with_retry",
    "_write_artifacts",
    "build_run_context",
    "execute_run",
    "refresh_artifact",
    "run_lint_tools_simple",
]


def build_run_context(
    *,
    action: str | Action,
    output_format: str,
    score: bool = False,
    debug: bool = False,
    no_art: bool = False,
    dry_run: bool = False,
) -> RunContext:
    """Create the run-scoped state shared by the execute and render phases.

    Initializes the output manager and run directory, sets up file logging,
    creates the console logger, and resolves the output-mode flags that both
    phases consult.

    Args:
        action: Action to perform ("check", "fmt", "test").
        output_format: Output format requested for the run.
        score: Whether stdout must carry only the numeric health score.
        debug: Whether to show DEBUG messages on the console.
        no_art: Whether to suppress the decorative ASCII art.
        dry_run: Whether this is a ``fmt --dry-run`` preview.

    Returns:
        RunContext: The shared context for this run.
    """
    resolved_action = normalize_action(action)

    # Dry-run preview: show what `fmt` WOULD fix without writing. Select the
    # fixable tool set (via the original fmt action) but execute, aggregate,
    # and compute the exit code in read-only check mode, so no files are
    # modified and the reported issues are exactly what a real fmt run would
    # address.
    selection_action = resolved_action
    dry_run_preview = dry_run and resolved_action == Action.FIX
    if dry_run_preview:
        resolved_action = Action.CHECK

    output_manager = OutputManager()

    # Initialize Loguru logging (must happen before any logger.debug() calls)
    from lintro.utils.logger_setup import setup_execution_logging

    setup_execution_logging(output_manager.run_dir, debug=debug)

    from lintro.config.config_loader import get_config
    from lintro.utils.console import create_logger

    # Explicit non-grid formats that must emit a single clean, parseable
    # document on stdout. For these we route all decorative console UI to
    # stderr and suppress the human summary so stdout carries only the payload
    # (grid remains the default human view).
    clean_stdout_output = output_format.lower() in ("json", "sarif", "csv", "markdown")
    # Score-only takes priority over machine-readable formats so
    # ``--score --output-format json`` still prints only the numeric score.
    score_only = bool(score)

    lintro_config = get_config()

    # Resolve whether decorative ASCII art may be shown. Either the ``--no-art``
    # flag or ``output.art: false`` in config disables it; the TTY guard in
    # print_ascii_art still applies on top of this.
    art_enabled = bool(lintro_config.output.art) and not no_art

    logger = create_logger(
        run_dir=output_manager.run_dir,
        route_stderr=clean_stdout_output or score_only,
        art_enabled=art_enabled,
    )

    return RunContext(
        action=resolved_action,
        selection_action=selection_action,
        dry_run_preview=dry_run_preview,
        output_manager=output_manager,
        logger=logger,
        lintro_config=lintro_config,
        clean_stdout_output=clean_stdout_output,
        score_only=score_only,
    )


def _execute_tools_parallel(
    *,
    ctx: RunContext,
    tools_to_run: list[str],
    paths: list[str],
    config_manager: UnifiedConfigManager,
    tool_option_dict: dict[str, Any],
    exclude: str | None,
    include_venv: bool,
    post_tools: set[str],
    incremental: bool,
    effective_auto_install: bool,
    diff_base: str | None,
    on_tool_result: Callable[[ToolResult], None] | None,
) -> list[ToolResult]:
    """Run every selected tool concurrently and collect their results.

    Args:
        ctx: Shared run context.
        tools_to_run: Names of the tools to execute.
        paths: Scan targets.
        config_manager: Shared unified configuration manager.
        tool_option_dict: Parsed ``--tool-options`` mapping.
        exclude: Exclude patterns.
        include_venv: Whether to include virtual environment directories.
        post_tools: Tools reserved for the post-check phase.
        incremental: Whether to only scan files changed since the last run.
        effective_auto_install: Resolved auto-install setting.
        diff_base: Resolved ``--diff`` base ref, or ``None``.
        on_tool_result: Optional per-result display callback.

    Returns:
        list[ToolResult]: Results for every tool that ran.
    """
    logger = ctx.logger
    logger.console_output(
        text=f"Running {len(tools_to_run)} tools in parallel "
        f"(max {ctx.lintro_config.execution.max_workers} workers)",
    )
    all_results = run_tools_parallel(
        tools_to_run=tools_to_run,
        paths=paths,
        action=ctx.action,
        config_manager=config_manager,
        tool_option_dict=tool_option_dict,
        exclude=exclude,
        include_venv=include_venv,
        post_tools=post_tools,
        max_workers=ctx.lintro_config.execution.max_workers,
        incremental=incremental,
        auto_install=effective_auto_install,
        max_fix_retries=ctx.lintro_config.execution.max_fix_retries,
        diff_base=diff_base,
    )

    # Enrich parallel results with doc_url from each plugin
    for result in all_results:
        try:
            tool = tool_manager.get_tool(result.name)
            _enrich_issues_with_doc_urls(tool, result)
        except (KeyError, ValueError):
            pass  # Tool not found — skip enrichment

    # Dry-run: restrict each result to would-fix issues before totals and
    # display so non-auto-fixable diagnostics don't inflate the count.
    if ctx.dry_run_preview:
        all_results = [_filter_result_to_fixable(r) for r in all_results]

    for result in all_results:
        # Print tool header like sequential mode does
        logger.print_tool_header(
            tool_name=get_tool_display_name(result.name),
            action=ctx.action,
        )
        if on_tool_result is not None:
            on_tool_result(result)

    return all_results


def _execute_tools_sequential(
    *,
    ctx: RunContext,
    tools_to_run: list[str],
    paths: list[str],
    config_manager: UnifiedConfigManager,
    tool_option_dict: dict[str, Any],
    exclude: str | None,
    include_venv: bool,
    post_tools: set[str],
    incremental: bool,
    effective_auto_install: bool,
    diff_base: str | None,
    on_tool_result: Callable[[ToolResult], None] | None,
) -> list[ToolResult]:
    """Run every selected tool one at a time and collect their results.

    Args:
        ctx: Shared run context.
        tools_to_run: Names of the tools to execute.
        paths: Scan targets.
        config_manager: Shared unified configuration manager.
        tool_option_dict: Parsed ``--tool-options`` mapping.
        exclude: Exclude patterns.
        include_venv: Whether to include virtual environment directories.
        post_tools: Tools reserved for the post-check phase.
        incremental: Whether to only scan files changed since the last run.
        effective_auto_install: Resolved auto-install setting.
        diff_base: Resolved ``--diff`` base ref, or ``None``.
        on_tool_result: Optional per-result display callback.

    Returns:
        list[ToolResult]: Results for every tool that ran, including synthetic
        failure results for tools that could not be initialized.

    Raises:
        TypeError: If a programming error occurs during tool execution.
        AttributeError: If a programming error occurs during tool execution.
    """
    logger = ctx.logger
    all_results: list[ToolResult] = []

    for tool_name in tools_to_run:
        try:
            tool = tool_manager.get_tool(tool_name)

            # Print tool header before execution
            logger.print_tool_header(
                tool_name=get_tool_display_name(tool_name),
                action=ctx.action,
            )

            # Configure tool using shared helper (returns a private
            # per-invocation copy; execute against it).
            tool = configure_tool_for_execution(
                tool=tool,
                tool_name=tool_name,
                config_manager=config_manager,
                tool_option_dict=tool_option_dict,
                exclude=exclude,
                include_venv=include_venv,
                incremental=incremental,
                action=ctx.action,
                post_tools=post_tools,
                auto_install=effective_auto_install,
                lintro_config=ctx.lintro_config,
                diff_base=diff_base,
            )

            # Execute the tool
            started = time.monotonic()
            if ctx.action == Action.FIX:
                result = _run_fix_with_retry(
                    tool=tool,
                    paths=paths,
                    options={},
                    max_retries=ctx.lintro_config.execution.max_fix_retries,
                )
            else:
                result = tool.check(paths, {})
            result.duration_seconds = time.monotonic() - started

            # Populate doc_url on each issue from the plugin
            _enrich_issues_with_doc_urls(tool, result)

            # Dry-run: restrict to issues a real fmt would actually fix so the
            # displayed tables, counts, and exit code exclude non-auto-fixable
            # check-mode diagnostics (e.g. ruff lint rules that are not
            # --fix-able).
            if ctx.dry_run_preview:
                result = _filter_result_to_fixable(result)

            all_results.append(result)

            if on_tool_result is not None:
                on_tool_result(result)

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
            all_results.append(
                ToolResult(
                    name=tool_name,
                    success=False,
                    output=f"Failed to initialize tool: {e}",
                    issues_count=0,
                ),
            )

    return all_results


def execute_run(
    *,
    ctx: RunContext,
    paths: list[str],
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
    include_venv: bool,
    group_by: str,
    output_format: str,
    verbose: bool,
    raw_output: bool = False,
    incremental: bool = False,
    auto_install: bool = False,
    yes: bool = False,
    ignore_conflicts: bool = False,
    fail_under: float | None = None,
    diff_base: str | None = None,
    ai_status_lines: list[str] | None = None,
    on_tool_result: Callable[[ToolResult], None] | None = None,
) -> RunArtifact:
    """Run the selected tools and aggregate everything the render phase needs.

    This is the execute half of the split described in issue #1823. It writes
    no files, emits no output document, and never imports :mod:`lintro.ai`.
    Progress messages and per-tool tables still reach the console, the latter
    via the injected ``on_tool_result`` callback, so nothing about the live
    experience changes.

    Args:
        ctx: Shared run context from :func:`build_run_context`.
        paths: List of paths to check.
        tools: Comma-separated list of tools to run.
        tool_options: Additional tool options.
        exclude: Patterns to exclude.
        include_venv: Whether to include virtual environments.
        group_by: How to group results (used by post-checks).
        output_format: Output format for results.
        verbose: Whether to enable verbose output.
        raw_output: Whether to show raw tool output instead of formatted output.
        incremental: Whether to only check files changed since last run.
        auto_install: Whether to auto-install Node.js deps if node_modules is
            missing.
        yes: Skip confirmation prompt and proceed immediately.
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        fail_under: When set, force exit code 1 if the computed health score
            is strictly below this threshold (CI gate).
        diff_base: Git base ref for ``--diff`` scanning. ``None`` scans all
            files; :data:`~lintro.utils.git_diff.DIFF_DEFAULT_SENTINEL`
            resolves the repository default base; any other value is used as
            the base ref. Non-git directories fall back to a full scan with a
            warning.
        ai_status_lines: Pre-rendered AI rows for the configuration summary,
            supplied by callers that have an AI layer. ``None`` renders the
            summary without AI rows.
        on_tool_result: Optional callback invoked with each completed tool
            result so it can be displayed live. ``None`` runs silently.

    Programming errors raised while a tool executes (``TypeError``,
    ``AttributeError``) propagate to the caller rather than being folded into a
    failed result, so they stay debuggable.

    Returns:
        RunArtifact: The results, totals, health score, and exit code for the
        run. ``early_exit`` is set when the run stopped before any tool ran.
    """
    logger = ctx.logger

    # Get tools to run (returns ToolsToRunResult with skip info)
    try:
        tools_result = get_tools_to_run(
            tools,
            ctx.selection_action,
            ignore_conflicts=ignore_conflicts,
        )
    except ValueError as e:
        logger.console_output(f"Error: {e}")
        return RunArtifact(
            action=ctx.action,
            exit_code=DEFAULT_EXIT_CODE_FAILURE,
            early_exit=True,
        )

    tools_to_run = tools_result.to_run
    skipped_tools = tools_result.skipped

    # On a no-config first run the toolset is scoped to detected languages;
    # tell the user what was selected and how to customize. Suppressed for
    # machine-readable output and score-only mode.
    if (
        tools_result.scoped_by_detection
        and output_format.lower() not in {"json", "sarif"}
        and not ctx.score_only
    ):
        from lintro.utils.execution.tool_configuration import format_detection_notice

        logger.console_output(
            text=format_detection_notice(
                tools_result.detected_languages,
                tools_to_run,
            ),
            color="cyan",
        )

    if not tools_to_run and not skipped_tools:
        logger.console_output("No tools to run.")
        return finalize_artifact(
            ctx=ctx,
            all_results=[],
            total_issues=0,
            total_fixed=0,
            total_remaining=0,
            main_phase_empty_due_to_filter=False,
            fail_under=fail_under,
        )

    if not tools_to_run and skipped_tools:
        report_skipped_tools(
            skipped_tools=skipped_tools,
            output_format=output_format,
            logger=logger,
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
    # that's okay - post-checks will still run. Track this state so we can
    # return failure if post-checks don't run either.
    main_phase_empty_due_to_filter = bool(not tools_to_run and post_tools_early)
    if main_phase_empty_due_to_filter:
        logger.console_output(
            text=(
                "All selected tools are configured as post-checks - skipping main phase"
            ),
        )

    # Print main header with output directory information
    logger.print_lintro_header()

    # Announce dry-run mode so users know no files will be modified.
    if ctx.dry_run_preview and output_format.lower() not in {"json", "sarif"}:
        logger.console_output(
            text="Dry run - no files modified",
            color="yellow",
        )

    # Show incremental mode message
    if incremental:
        logger.console_output(
            text="Incremental mode: only checking files changed since last run",
            color="cyan",
        )

    diff_scope = resolve_diff_scope(
        diff_base=diff_base,
        paths=paths,
        logger=logger,
    )
    if diff_scope.failed:
        return RunArtifact(
            action=ctx.action,
            exit_code=DEFAULT_EXIT_CODE_FAILURE,
            early_exit=True,
        )
    resolved_diff_base = diff_scope.base

    # Parse tool options once for all tools
    from lintro.utils.tool_options import parse_tool_options

    tool_option_dict = parse_tool_options(tool_options)

    # Create UnifiedConfigManager once before the loop
    config_manager = UnifiedConfigManager()

    lintro_config = ctx.lintro_config
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

    # Pre-execution config summary. Suppressed for clean-stdout formats
    # (json/sarif/csv/markdown) and score-only mode because it writes the rich
    # Configuration box to stdout via its own Console, bypassing route_stderr.
    if (
        not ctx.clean_stdout_output
        and not ctx.score_only
        and (tools_to_run or skipped_tools)
    ):
        proceed = confirm_pre_execution(
            tools_to_run=tools_to_run,
            skipped_tools=skipped_tools,
            lintro_config=lintro_config,
            effective_auto_install=effective_auto_install,
            is_container=is_container,
            ai_status_lines=ai_status_lines,
            logger=logger,
            yes=yes,
        )
        if not proceed:
            return RunArtifact(
                action=ctx.action,
                exit_code=DEFAULT_EXIT_CODE_SUCCESS,
                early_exit=True,
            )

    execute_tools = (
        _execute_tools_parallel if use_parallel else _execute_tools_sequential
    )
    all_results = execute_tools(
        ctx=ctx,
        tools_to_run=tools_to_run,
        paths=paths,
        config_manager=config_manager,
        tool_option_dict=tool_option_dict,
        exclude=exclude,
        include_venv=include_venv,
        post_tools=post_tools_early,
        incremental=incremental,
        effective_auto_install=effective_auto_install,
        diff_base=resolved_diff_base,
        on_tool_result=on_tool_result,
    )

    if use_parallel:
        total_issues, total_fixed, total_remaining = aggregate_tool_results(
            all_results,
            ctx.action,
        )
    else:
        total_issues, total_fixed, total_remaining = sequential_totals(
            all_results,
            ctx.action,
        )

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
        action=ctx.action,
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
        diff_base=resolved_diff_base,
    )

    # Dry-run: post-checks may append additional check-mode results. Restrict
    # every result to its would-fix subset and re-derive the totals so the
    # summary and exit code count only auto-fixable issues.
    if ctx.dry_run_preview:
        all_results[:] = [
            r if getattr(r, "skipped", False) else _filter_result_to_fixable(r)
            for r in all_results
        ]
        total_issues, total_fixed, total_remaining = aggregate_tool_results(
            all_results,
            ctx.action,
        )

    return finalize_artifact(
        ctx=ctx,
        all_results=all_results,
        total_issues=total_issues,
        total_fixed=total_fixed,
        total_remaining=total_remaining,
        main_phase_empty_due_to_filter=main_phase_empty_due_to_filter,
        fail_under=fail_under,
    )


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
    ai_fix: bool = False,
    ignore_conflicts: bool = False,
    transport: str | None = None,
    dry_run: bool = False,
    score: bool = False,
    fail_under: float | None = None,
    diff_base: str | None = None,
    no_art: bool = False,
) -> int:
    """Run tools and render their output, returning the process exit code.

    A thin wrapper over :func:`build_run_context`, :func:`execute_run`, and
    :func:`lintro.utils.execution.run_renderer.render_run`. It runs no AI:
    callers that want AI enhancement drive the three phases themselves and
    insert :func:`lintro.ai.interface.enhance_artifact` between them.

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
        ai_fix: Accepted for signature compatibility; this wrapper runs no AI.
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        transport: Accepted for signature compatibility; this wrapper runs no AI.
        dry_run: Preview what ``fmt`` would fix without modifying files. When
            set with a ``fmt`` action, tools run in read-only check mode using
            the fixable tool set; the reported issues are exactly what a real
            ``fmt`` run would address. Exit code mirrors check semantics: 0 when
            nothing would be fixed, 1 when fixes are available.
        score: When True with human-readable output, print only the 0-100
            health score line and suppress the normal execution summary.
        fail_under: When set, exit with code 1 if the computed health score is
            strictly below this threshold (CI gate).
        diff_base: Git base ref for ``--diff`` scanning. ``None`` scans all
            files; :data:`~lintro.utils.git_diff.DIFF_DEFAULT_SENTINEL` resolves
            the repository default base; any other value is used as the base
            ref. Non-git directories fall back to a full scan with a warning.
        no_art: When True, suppress decorative ASCII art regardless of the
            ``output.art`` config value. Art is also suppressed automatically
            when ``output.art`` is ``False`` or stdout is not a TTY.

    Programming errors raised while a tool executes (``TypeError``,
    ``AttributeError``) propagate to the caller.

    Returns:
        Exit code (0 for success, 1 for failures).
    """
    ctx = build_run_context(
        action=action,
        output_format=output_format,
        score=score,
        debug=debug,
        no_art=no_art,
        dry_run=dry_run,
    )
    from lintro.utils.execution.run_renderer import make_result_display

    artifact = execute_run(
        ctx=ctx,
        paths=paths,
        tools=tools,
        tool_options=tool_options,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        incremental=incremental,
        auto_install=auto_install,
        yes=yes,
        ignore_conflicts=ignore_conflicts,
        fail_under=fail_under,
        diff_base=diff_base,
        on_tool_result=make_result_display(
            logger=ctx.logger,
            output_format=output_format,
            raw_output=raw_output,
            action=ctx.action,
        ),
    )
    render_run(
        artifact,
        ctx=ctx,
        output_format=output_format,
        output_file=output_file,
    )
    return artifact.exit_code
