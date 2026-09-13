"""Execute tools into a run artifact and optionally render its output.

The execute/render split from issue #1823 keeps execution AI-free while
``run_lint_tools_simple`` preserves the legacy one-call exit-code contract.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from lintro.enums.action import Action, normalize_action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager, verify_pass
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
from lintro.utils.gates import execute_gates
from lintro.utils.output import OutputManager
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
    debug: bool = False,
    no_art: bool = False,
    dry_run: bool = False,
    group_by: str = "auto",
    profile: bool = False,
) -> RunContext:
    """Create the run-scoped state shared by the execute and render phases.

    Initializes the output manager and run directory, sets up file logging,
    creates the console logger, and resolves the output-mode flags that both
    phases consult.

    Args:
        action: Action to perform ("check", "fmt", "test").
        output_format: Output format requested for the run.
        debug: Whether to show DEBUG messages on the console.
        no_art: Whether to suppress the decorative ASCII art.
        dry_run: Whether this is a ``fmt --dry-run`` preview.
        group_by: How to group issues in formatted and JSON output.
        profile: Whether to emit the human/JSON performance profile. Main-tool
            timings are recorded regardless; this flag only gates rendering.

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

    lintro_config = get_config()

    # Resolve whether decorative ASCII art may be shown. Either the ``--no-art``
    # flag or ``output.art: false`` in config disables it; the TTY guard in
    # print_ascii_art still applies on top of this.
    art_enabled = bool(lintro_config.output.art) and not no_art

    logger = create_logger(
        run_dir=output_manager.run_dir,
        route_stderr=clean_stdout_output,
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
        group_by=group_by,
        profile=profile,
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
    selected_tools: set[str],
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
        selected_tools: Every tool selected for this run, used to
            resolve per-pattern format authority.
        incremental: Whether to only scan files changed since the last run.
        effective_auto_install: Resolved auto-install setting.
        diff_base: Resolved ``--diff`` base ref, or ``None``.
        on_tool_result: Optional per-result display callback.

    Returns:
        list[ToolResult]: Results for every tool that ran.
    """
    logger = ctx.logger
    if ctx.action == Action.FIX:
        # Say what actually happens: the mutation phase runs one tool at a
        # time (#1743), so announcing a worker count here would be a lie.
        logger.console_output(
            text=(
                f"Running {len(tools_to_run)} tools, one at a time "
                "(mutating tools are not run concurrently)"
            ),
        )
    else:
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
        selected_tools=selected_tools,
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
        except (KeyError, OSError, ValueError, RuntimeError):
            # Unresolvable tool: the parallel dispatcher already recorded a
            # failure result for it, so there is nothing to enrich.
            continue

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
    selected_tools: set[str],
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
        selected_tools: Every tool selected for this run, used to
            resolve per-pattern format authority.
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
        attempt_started = time.monotonic()
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
                selected_tools=selected_tools,
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

            # Create a failed result for this tool. Duration is recorded so
            # crashed tools still appear in the ``--profile`` table/JSON.
            all_results.append(
                ToolResult(
                    name=tool_name,
                    success=False,
                    output=f"Failed to initialize tool: {e}",
                    issues_count=0,
                    duration_seconds=time.monotonic() - attempt_started,
                ),
            )

    return all_results


def _run_verify_phase(
    *,
    ctx: RunContext,
    baseline: verify_pass.VerifyBaseline,
    tools_to_run: list[str],
    all_results: list[ToolResult],
    config_manager: UnifiedConfigManager,
    tool_option_dict: dict[str, Any],
    exclude: str | None,
    include_venv: bool,
    effective_auto_install: bool,
    diff_base: str | None,
) -> None:
    """Run the single verify pass and fold its residual into the run.

    This is the verify half of the mutate-then-verify pipeline (#1743). The
    ``CHECK`` capability of every selected tool that declares one runs exactly
    once, after every mutating capability has finished, over the files whose
    fingerprint moved. Its findings replace the residual those tools reported
    for themselves, so a residual is counted once and cross-tool interference
    is visible. A mutator that declares no ``CHECK`` (prettier, oxfmt, rustfmt,
    shfmt) is not verified here and keeps its own counts until #2607.

    Does nothing outside a ``fmt`` run, or when no selected tool declares a
    pattern-addressed mutating claim.

    Args:
        ctx: Shared run context.
        baseline: Fingerprints captured before the mutation phase.
        tools_to_run: Tools selected for the run, in execution order.
        all_results: Mutation-phase results, folded in place.
        config_manager: Shared unified configuration manager.
        tool_option_dict: Parsed ``--tool-options`` mapping.
        exclude: Exclude patterns.
        include_venv: Whether to include virtual environment directories.
        effective_auto_install: Resolved auto-install setting.
        diff_base: Resolved ``--diff`` base ref, or ``None``.
    """
    # Self-guarding rather than trusting the caller's empty-baseline sentinel:
    # the docstring above promises this is a no-op outside a real ``fmt`` run,
    # and that promise should hold locally rather than by a convention shared
    # with ``execute_run``.
    if ctx.action != Action.FIX or ctx.dry_run_preview:
        return
    if not baseline.candidates:
        return

    scope = verify_pass.resolve_verify_scope(baseline)

    def _configure_for_verify(*, tool_name: str) -> verify_pass.VerifiableTool:
        """Build the check-mode plugin copy the verify pass executes.

        Args:
            tool_name: Registry key of the tool to configure.

        Returns:
            VerifiableTool: The configured per-invocation plugin copy.
        """
        return configure_tool_for_execution(
            tool=tool_manager.get_tool(tool_name),
            tool_name=tool_name,
            config_manager=config_manager,
            tool_option_dict=tool_option_dict,
            exclude=exclude,
            include_venv=include_venv,
            # The verify pass does its own narrowing; layering the incremental
            # cache on top would make the residual depend on a previous run.
            incremental=False,
            action=Action.CHECK,
            selected_tools=set(tools_to_run),
            auto_install=effective_auto_install,
            lintro_config=ctx.lintro_config,
            diff_base=diff_base,
        )

    if not ctx.clean_stdout_output:
        # The per-tool tables above were streamed by the mutation phase, so
        # their counts are pre-verify. Say so: the summary below can disagree
        # with them, and a reader who is not told will trust the first number
        # they saw.
        ctx.logger.console_output(
            text=(
                f"Verify pass: re-checking {scope.summary} "
                "(per-tool counts above are provisional; the summary below is "
                "authoritative)"
            ),
            color="cyan",
        )

    # Only tools whose mutation result the fold can actually use are worth
    # verifying. ``fold_verify_results`` discards the outcome of a tool that
    # was skipped or burned its deadline during ``fix``, so configuring and
    # running its ``CHECK`` would spend a whole tool invocation on a result
    # that is thrown away.
    foldable = [
        name
        for name in tools_to_run
        if not any(r.name == name and (r.skipped or r.timed_out) for r in all_results)
    ]

    verify_outcomes = verify_pass.run_verify_pass(
        tools_to_run=foldable,
        scope=scope,
        configure=_configure_for_verify,
    )
    verify_pass.fold_verify_results(
        mutation_results=all_results,
        verify_results=verify_outcomes,
        scope=scope,
    )


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
    run_gates: bool = True,
    ignore_conflicts: bool = False,
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
        group_by: How to group results.
        output_format: Output format for results.
        verbose: Whether to enable verbose output.
        raw_output: Whether to show raw tool output instead of formatted output.
        incremental: Whether to only check files changed since last run.
        auto_install: Whether to auto-install Node.js deps if node_modules is
            missing.
        yes: Skip confirmation prompt and proceed immediately.
        run_gates: Whether the run-level gates (module size, duplicate
            code) may run.
        ignore_conflicts: Whether to ignore tool configuration conflicts.
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
        RunArtifact: The results, totals, severity tallies, and exit code for
        the run. ``early_exit`` is set when the run stopped before any tool ran.
    """
    logger = ctx.logger

    # Get tools to run (returns ToolsToRunResult with skip info)
    try:
        tools_result = get_tools_to_run(
            tools,
            ctx.selection_action,
            ignore_conflicts=ignore_conflicts,
            scan_roots=list(paths),
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
    # machine-readable stdout.
    if tools_result.scoped_by_detection and not ctx.clean_stdout_output:
        from lintro.utils.execution.tool_configuration import format_detection_notice

        logger.console_output(
            text=format_detection_notice(
                detected_languages=tools_result.detected_languages,
                to_run=tools_to_run,
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
        )

    if not tools_to_run and skipped_tools:
        report_skipped_tools(
            skipped_tools=skipped_tools,
            output_format=output_format,
            logger=logger,
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
    # (json/sarif/csv/markdown) because it writes the rich Configuration box
    # to stdout via its own Console, bypassing route_stderr.
    if not ctx.clean_stdout_output and (tools_to_run or skipped_tools):
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

    # Mutate-then-verify (#1743). Fingerprint every file a mutating capability
    # could rewrite *before* the mutation phase, so the verify pass that
    # follows can be narrowed to the files that actually moved. ``chk`` and
    # the ``fmt --dry-run`` preview stay read-only and take no snapshot.
    verify_baseline = verify_pass.VerifyBaseline(candidates=())
    if ctx.action == Action.FIX and not ctx.dry_run_preview:
        verify_baseline = verify_pass.capture_verify_baseline(
            tools_to_run=tools_to_run,
            paths=paths,
            exclude=exclude,
            include_venv=include_venv,
            # Scoped exactly like the mutation phase: a floor fallback must
            # never re-check files this run could not have touched.
            incremental=incremental,
            diff_base=resolved_diff_base,
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
        selected_tools=set(tools_to_run),
        incremental=incremental,
        effective_auto_install=effective_auto_install,
        diff_base=resolved_diff_base,
        on_tool_result=on_tool_result,
    )

    for result in all_results:
        if result.capability is None and not result.skipped:
            result.capability = verify_pass.resolve_result_capability(
                tool_name=result.name,
                action=ctx.action,
            )

    _run_verify_phase(
        ctx=ctx,
        baseline=verify_baseline,
        tools_to_run=tools_to_run,
        all_results=all_results,
        config_manager=config_manager,
        tool_option_dict=tool_option_dict,
        exclude=exclude,
        include_venv=include_venv,
        effective_auto_install=effective_auto_install,
        diff_base=resolved_diff_base,
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

    # Run the run-level gates (module size, duplicate code) over the results.
    if run_gates:
        total_issues = execute_gates(
            action=ctx.action,
            paths=paths,
            exclude=exclude,
            include_venv=include_venv,
            output_format=output_format,
            logger=logger,
            all_results=all_results,
            total_issues=total_issues,
        )

    # Dry-run: a gate may append an additional check-mode result. Restrict
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
    run_gates: bool = True,
    ai_fix: bool = False,
    ignore_conflicts: bool = False,
    transport: str | None = None,
    dry_run: bool = False,
    diff_base: str | None = None,
    no_art: bool = False,
    on_tool_result: Callable[[ToolResult], None] | None = None,
    render_summary: bool = True,
    profile: bool = False,
) -> int:
    """Run tools and render their output, returning the process exit code.

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
        run_gates: Whether the run-level gates (module size, duplicate
            code) may run.
        ai_fix: Accepted for signature compatibility; this wrapper runs no AI.
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        transport: Accepted for signature compatibility; this wrapper runs no AI.
        dry_run: Preview what ``fmt`` would fix without modifying files. When
            set with a ``fmt`` action, tools run in read-only check mode using
            the fixable tool set; the reported issues are exactly what a real
            ``fmt`` run would address. Exit code mirrors check semantics: 0 when
            nothing would be fixed, 1 when fixes are available.
        diff_base: Git base ref for ``--diff`` scanning. ``None`` scans all
            files; :data:`~lintro.utils.git_diff.DIFF_DEFAULT_SENTINEL` resolves
            the repository default base; any other value is used as the base
            ref. Non-git directories fall back to a full scan with a warning.
        no_art: When True, suppress decorative ASCII art regardless of the
            ``output.art`` config value. Art is also suppressed automatically
            when ``output.art`` is ``False`` or stdout is not a TTY.
        on_tool_result: Optional custom live renderer for each completed tool.
        render_summary: Whether to render the normal final report and summary.
        profile: When True, render a per-tool performance profile after the run
            and include the profile payload in JSON output.

    Programming errors raised while a tool executes (``TypeError``,
    ``AttributeError``) propagate to the caller.

    Returns:
        Exit code (0 for success, 1 for failures).
    """
    ctx = build_run_context(
        action=action,
        output_format=output_format,
        debug=debug,
        no_art=no_art,
        dry_run=dry_run,
        group_by=group_by,
        profile=profile,
    )
    try:
        from lintro.utils.execution.run_renderer import make_result_display

        result_display = on_tool_result or make_result_display(
            logger=ctx.logger,
            output_format=output_format,
            raw_output=raw_output,
            action=ctx.action,
            group_by=group_by,
        )
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
            run_gates=run_gates,
            ignore_conflicts=ignore_conflicts,
            diff_base=diff_base,
            on_tool_result=result_display,
        )
        if render_summary:
            render_run(
                artifact,
                ctx=ctx,
                output_format=output_format,
                output_file=output_file,
            )
        return artifact.exit_code
    finally:
        ctx.output_manager.mark_run_complete()
        try:
            ctx.output_manager.cleanup_old_runs()
        except OSError as exc:
            ctx.logger.warning(f"Warning: Failed to clean up old runs: {exc}")
