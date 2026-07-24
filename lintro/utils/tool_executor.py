"""Helper functions for tool execution.

Clean, straightforward approach using Loguru with rich formatting:
1. OutputManager - handles structured output files only
2. ThreadSafeConsoleLogger - handles console display with thread-safe message
   tracking for parallel execution
3. No tee, no stream redirection, no complex state management

Supports parallel execution when enabled via configuration.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

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
    from collections.abc import Callable, Sequence

    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin

# Re-export constants for backwards compatibility
__all__ = [
    "DEFAULT_EXIT_CODE_FAILURE",
    "DEFAULT_EXIT_CODE_SUCCESS",
    "DEFAULT_REMAINING_COUNT",
    "run_lint_tools_simple",
]


def _write_stdout_verbatim(payload: str) -> None:
    r"""Write ``payload`` to stdout without newline translation.

    The CSV renderer emits RFC 4180 ``\r\n`` line terminators. Writing that
    through the text-mode ``sys.stdout`` wrapper would translate every ``\n``
    a second time on Windows, producing ``\r\r\n`` and breaking the
    byte-for-byte equality between the stdout payload and the
    ``--output <file>.csv`` artifact (#1665).

    Writing UTF-8 bytes straight to ``sys.stdout.buffer`` bypasses translation
    on every platform. Streams without a binary ``buffer`` (for example a
    ``StringIO`` substituted by a caller) fall back to a plain text write,
    which is already correct because such streams perform no translation.

    Args:
        payload: The exact document to emit on stdout.
    """
    stream = sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(payload)
        stream.flush()
        return
    # Flush any pending text writes first so byte output stays ordered.
    stream.flush()
    buffer.write(payload.encode("utf-8"))
    buffer.flush()


def _get_remaining_count(result: ToolResult) -> int:
    """Get remaining issue count from a ToolResult.

    Falls back to issues_count when remaining_issues_count is not set,
    then to 0 if neither is available.

    Args:
        result: The tool result to inspect.

    Returns:
        int: Number of remaining issues.
    """
    if result.remaining_issues_count is not None:
        return result.remaining_issues_count
    if result.issues_count is not None:
        return result.issues_count
    return 0


def _run_fix_with_retry(
    tool: BaseToolPlugin,
    paths: list[str],
    options: dict[str, object],
    max_retries: int,
) -> ToolResult:
    """Run tool.fix() with convergence retries.

    Some formatters (e.g. prettier with proseWrap) are non-idempotent and
    need multiple write→verify cycles to stabilize. This function retries
    fix() up to ``max_retries`` times, keeping the initial issue count from
    the first pass and the remaining count from the last pass.

    Args:
        tool: The tool plugin to execute.
        paths: List of file paths to process.
        options: Runtime options for the tool.
        max_retries: Maximum number of fix→verify cycles.

    Returns:
        ToolResult: Merged result across all passes.
    """
    from loguru import logger

    result = tool.fix(paths, options)

    if max_retries <= 1:
        return result

    initial_issues_count = getattr(result, "initial_issues_count", None)
    first_pass_initial_issues = getattr(result, "initial_issues", None)
    remaining = _get_remaining_count(result)

    for attempt in range(2, max_retries + 1):
        if remaining == 0:
            break

        logger.debug(
            f"Fix retry {attempt}/{max_retries} for "
            f"{getattr(getattr(tool, 'definition', None), 'name', 'unknown')} "
            f"({remaining} remaining issues)",
        )
        result = tool.fix(paths, options)
        remaining = _get_remaining_count(result)

    # Merge: keep initial_issues_count and initial_issues from first pass,
    # rest from last pass
    if initial_issues_count is not None:
        fixed = max(0, initial_issues_count - remaining)
        result = ToolResult(
            name=result.name,
            success=result.success,
            output=result.output,
            issues_count=remaining,
            issues=result.issues,
            initial_issues_count=initial_issues_count,
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
            formatted_output=result.formatted_output,
            initial_issues=first_pass_initial_issues,
            cwd=result.cwd,
        )
    elif first_pass_initial_issues is not None:
        # Preserve initial_issues even when initial_issues_count is absent
        fixed = max(0, len(first_pass_initial_issues) - remaining)
        result = ToolResult(
            name=result.name,
            success=result.success,
            output=result.output,
            issues_count=remaining,
            issues=result.issues,
            initial_issues_count=len(first_pass_initial_issues),
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
            formatted_output=result.formatted_output,
            initial_issues=first_pass_initial_issues,
            cwd=result.cwd,
        )

    return result


def _warn_ai_fix_disabled(
    *,
    action: Action,
    ai_fix: bool,
    ai_lint_enabled: bool,
    logger: Any,
    output_format: str = "",
) -> None:
    """Warn when users request AI fixes but AI lint is disabled in config."""
    if action != Action.CHECK or not ai_fix or ai_lint_enabled:
        return
    # Suppress plain-text warnings for machine-readable output formats
    if output_format.lower() in ("json", "sarif"):
        return
    logger.console_output(
        "AI fixes requested with --fix, but AI lint is disabled in "
        ".lintro-config.yaml (set ai.enabled and ai.lint: true); "
        "skipping AI enhancements.",
    )


def _display_fix_result(
    result: ToolResult,
    *,
    output_format: str,
    raw_output: bool,
    console_output_func: Callable[..., None],
    success_func: Callable[..., None],
    action: Action,
) -> None:
    """Display fix result with initial issue details when available.

    When a tool fixes issues, this shows WHAT was fixed (via initial_issues)
    before showing the count summary. Falls back to the standard display
    when initial_issues is not populated.

    Args:
        result: The tool result to display.
        output_format: Output format for formatting issues.
        raw_output: Whether to show raw tool output.
        console_output_func: Function to output text to console.
        success_func: Function to display success message.
        action: The action being performed.
    """
    from lintro.formatters import format_fix_results
    from lintro.utils.output import format_tool_output
    from lintro.utils.result_formatters import print_tool_result

    # When in fix mode and initial_issues is populated, show two tables:
    # "Detected issues" (pre-fix) and "Remaining issues" (post-fix).
    if action == Action.FIX and result.initial_issues and not raw_output:
        remaining_issues = list(result.issues) if result.issues else None
        issues_display = format_fix_results(
            detected_issues=list(result.initial_issues),
            remaining_issues=remaining_issues,
            output_format=output_format,
            tool_name=result.name,
        )
        if issues_display and issues_display.strip():
            console_output_func(text=issues_display)

        # Show the count summary below the tables
        print_tool_result(
            console_output_func=console_output_func,
            success_func=success_func,
            tool_name=result.name,
            output=result.output or "",
            issues_count=result.issues_count,
            raw_output_for_meta=result.output,
            action=action,
            success=result.success,
            ai_metadata=result.ai_metadata,
            parse_failures_count=result.parse_failures_count or 0,
        )
        return

    # Standard display path (no initial_issues available)
    display_output: str | None = None
    if result.formatted_output:
        display_output = result.formatted_output
    elif result.issues or result.output:
        display_output = format_tool_output(
            tool_name=result.name,
            output=result.output or "",
            output_format=output_format,
            issues=list(result.issues) if result.issues else None,
            success=result.success,
            issues_count=result.issues_count,
        )
    if result.output and raw_output:
        display_output = result.output

    if display_output and display_output.strip():
        print_tool_result(
            console_output_func=console_output_func,
            success_func=success_func,
            tool_name=result.name,
            output=display_output,
            issues_count=result.issues_count,
            raw_output_for_meta=result.output,
            action=action,
            success=result.success,
            ai_metadata=result.ai_metadata,
            parse_failures_count=result.parse_failures_count or 0,
        )
    elif (
        result.issues_count == 0
        and result.success
        and not getattr(result, "fixed_issues_count", 0)
    ):
        print_tool_result(
            console_output_func=console_output_func,
            success_func=success_func,
            tool_name=result.name,
            output="",
            issues_count=0,
            action=action,
            success=result.success,
            ai_metadata=result.ai_metadata,
            parse_failures_count=result.parse_failures_count or 0,
        )


_ARTIFACT_EXTENSIONS: dict[str, str] = {
    "json": "results.json",
    "csv": "results.csv",
    "markdown": "results.md",
    "html": "results.html",
    "sarif": "results.sarif.json",
    "plain": "results.txt",
}


def _write_artifacts(
    all_results: list[ToolResult],
    lintro_config: Any,
    logger: Any,
    action: Action,
    total_issues: int,
    total_fixed: int,
    *,
    warn_func: Any = None,
) -> None:
    """Write side-channel artifact files alongside primary output.

    Emits artifact files into ``.lintro/artifacts/<format>/`` for each
    format listed in ``execution.artifacts``.  SARIF is also auto-emitted
    when ``GITHUB_ACTIONS=true`` is detected (for Code Scanning).

    Supported formats match ``OutputFormat``: json, csv, markdown,
    html, sarif, plain.

    Args:
        all_results: Completed tool results.
        lintro_config: Loaded LintroConfig instance.
        logger: Console logger for warning output.
        action: The action performed (check, fmt, test).
        total_issues: Total number of issues found.
        total_fixed: Total number of issues fixed.
        warn_func: Optional callback for emitting warnings.  When ``None``,
            falls back to ``logger.console_output``.
    """
    import os
    from pathlib import Path

    from lintro.enums.output_format import normalize_output_format
    from lintro.utils.output.file_writer import write_output_file

    artifacts: list[str] = [a.lower() for a in lintro_config.execution.artifacts]
    is_gha = os.environ.get("GITHUB_ACTIONS") == "true"

    # Auto-emit SARIF in GitHub Actions for Code Scanning integration.
    if is_gha and "sarif" not in artifacts:
        artifacts.append("sarif")

    if not artifacts:
        return

    _emit = warn_func if warn_func is not None else logger.console_output

    for artifact in artifacts:
        filename = _ARTIFACT_EXTENSIONS.get(artifact)
        if filename is None:
            _emit(f"Warning: Unknown artifact format '{artifact}', skipping")
            continue

        artifact_path = Path(".lintro") / "artifacts" / artifact / filename
        try:
            fmt = normalize_output_format(artifact)
            write_output_file(
                output_path=str(artifact_path),
                output_format=fmt,
                all_results=all_results,
                action=action,
                total_issues=total_issues,
                total_fixed=total_fixed,
            )
        except (OSError, ValueError, TypeError) as e:
            _emit(f"Warning: Failed to write {artifact} artifact: {e}")


def _enrich_issues_with_doc_urls(
    tool: BaseToolPlugin,
    result: ToolResult,
) -> None:
    """Populate doc_url on each issue using the plugin's doc_url method.

    Enriches both remaining issues (``result.issues``) and any pre-fix
    issues (``result.initial_issues``) so the fix-mode "Detected" and
    "Remaining" tables both show doc URLs. Skips issues that already
    have a doc_url set.

    Args:
        tool: Plugin instance that may provide a doc_url method.
        result: ToolResult whose issues will be enriched in-place.
    """
    if not hasattr(tool, "doc_url"):
        return

    def _enrich(issues: Sequence[BaseIssue] | None) -> None:
        if not issues:
            return
        for issue in issues:
            if getattr(issue, "doc_url", ""):
                continue
            # Resolve the code attribute via DISPLAY_FIELD_MAP so tools
            # that store their identifier under a different name (e.g.
            # advisory_id, vuln_id, rule_id) are handled correctly.
            field_map = getattr(issue, "DISPLAY_FIELD_MAP", {})
            code_attr = field_map.get("code", "code")
            code = str(getattr(issue, code_attr, "") or "")
            if code:
                url = tool.doc_url(code)
                if url:
                    issue.doc_url = url

    _enrich(result.issues)
    _enrich(result.initial_issues)


def _issue_would_be_fixed(issue: BaseIssue) -> bool:
    """Return whether a check-mode issue is one that ``fmt`` would auto-fix.

    Resolves the issue's fixability via ``DISPLAY_FIELD_MAP`` (some tools store
    the flag under a different attribute). Tools that carry a per-issue
    fixability signal are honored — only truthy-fixable issues count. For
    example, ruff sets ``fixable`` from ruff's own ``fix`` field, so its
    non-``--fix``-able lint diagnostics are excluded.

    Issue types that expose no ``fixable`` attribute at all (the pure-formatter
    parsers such as prettier, oxfmt, taplo, sqlfluff) carry no fixability
    distinction. Because dry-run only runs fix-capable (formatter) tools, every
    diagnostic such a tool reports in check mode represents a reformat that a
    real ``fmt`` run would apply, so it is treated as fixable.

    Args:
        issue: The parsed issue to classify.

    Returns:
        bool: True if the issue would be fixed by a real ``fmt`` run.
    """
    field_map = getattr(issue, "DISPLAY_FIELD_MAP", {})
    fixable_attr = field_map.get("fixable", "fixable")
    if not hasattr(issue, fixable_attr):
        return True
    return bool(getattr(issue, fixable_attr, False))


def _filter_result_to_fixable(result: ToolResult) -> ToolResult:
    """Return a copy of a dry-run check result limited to would-fix issues.

    Filters ``issues`` down to the auto-fixable subset (see
    :func:`_issue_would_be_fixed`) and updates ``issues_count`` to match, so
    dry-run counts, the summary line, and the exit code reflect only what a
    real ``fmt`` run would actually change rather than every check-mode
    diagnostic.

    A check-mode tool sets ``success=False`` when it merely *finds* issues, but
    in dry-run those diagnostics are informational: the exit code must derive
    purely from the fixable issue count, not from the tool having found
    (possibly non-fixable) issues. So any result that parsed issues is marked
    ``success=True`` here — it ran fine. Results without parsed issues are
    returned unchanged: a genuine execution failure carries no parsed issues
    and must still fail the run, and dry-run only runs fix-capable tools, so a
    reported change without structured issues is treated as fixable.

    Args:
        result: The check-mode result to filter.

    Returns:
        ToolResult: A filtered copy, or the original when there are no parsed
        issues.
    """
    import dataclasses

    if not result.issues:
        return result
    fixable = [issue for issue in result.issues if _issue_would_be_fixed(issue)]
    return dataclasses.replace(
        result,
        issues=fixable,
        issues_count=len(fixable),
        success=True,
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
        ai_fix: Enable AI fix suggestions with interactive review (check only).
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        transport: Optional CLI override for ``ai.transport`` when AI runs.
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

    Returns:
        Exit code (0 for success, 1 for failures).

    Raises:
        TypeError: If a programming error occurs during tool execution.
        AttributeError: If a programming error occurs during tool execution.
        Exception: Re-raised from AI hook when ``ai.fail_on_ai_error`` is enabled.
    """
    # Normalize action to enum
    action = normalize_action(action)

    # Dry-run preview: show what `fmt` WOULD fix without writing. Select the
    # fixable tool set (via the original fmt action) but execute, aggregate,
    # and compute the exit code in read-only check mode, so no files are
    # modified and the reported issues are exactly what a real fmt run would
    # address.
    selection_action = action
    dry_run_preview = dry_run and action == Action.FIX
    if dry_run_preview:
        action = Action.CHECK

    # Initialize output manager for this run
    output_manager = OutputManager()

    # Initialize Loguru logging (must happen before any logger.debug() calls)
    from lintro.utils.logger_setup import setup_execution_logging

    setup_execution_logging(output_manager.run_dir, debug=debug)

    # Create simplified logger with rich formatting.
    from lintro.utils.console import create_logger

    # Explicit non-grid formats that must emit a single clean, parseable
    # document on stdout. For these we route all decorative console UI to
    # stderr and suppress the human summary so stdout carries only the payload
    # (grid remains the default human view).
    clean_stdout_output = output_format.lower() in ("json", "sarif", "csv", "markdown")
    # Score-only takes priority over machine-readable formats so
    # ``--score --output-format json`` still prints only the numeric score.
    score_only = bool(score)

    # Resolve whether decorative ASCII art may be shown. Either the ``--no-art``
    # flag or ``output.art: false`` in config disables it; the TTY guard in
    # print_ascii_art still applies on top of this.
    from lintro.config.config_loader import get_config as _get_config

    art_enabled = bool(_get_config().output.art) and not no_art

    logger = create_logger(
        run_dir=output_manager.run_dir,
        route_stderr=clean_stdout_output or score_only,
        art_enabled=art_enabled,
    )

    # Get tools to run (now returns ToolsToRunResult with skip info)
    try:
        tools_result = get_tools_to_run(
            tools,
            selection_action,
            ignore_conflicts=ignore_conflicts,
        )
    except ValueError as e:
        logger.console_output(f"Error: {e}")
        return 1

    tools_to_run = tools_result.to_run
    skipped_tools = tools_result.skipped

    if not tools_to_run and not skipped_tools:
        logger.console_output("No tools to run.")
        from lintro.config.config_loader import get_config
        from lintro.utils.health_score import health_score_for_results

        empty_config = get_config()
        health = health_score_for_results(
            [],
            getattr(empty_config, "score", None),
        )
        exit_code = 0
        if fail_under is not None and health.score < fail_under:
            exit_code = 1
        if score_only:
            print(health.score)
        return exit_code

    if not tools_to_run and skipped_tools:
        _missing_keywords = ("not found", "missing")
        all_missing = all(
            st.reason and any(kw in st.reason.lower() for kw in _missing_keywords)
            for st in skipped_tools
        )
        from lintro.enums.output_format import OutputFormat, normalize_output_format

        fmt = normalize_output_format(output_format)
        machine_readable = fmt in (OutputFormat.JSON, OutputFormat.SARIF)
        if all_missing and not machine_readable:
            from lintro.cli_utils.onboarding import (
                is_interactive_tty,
                print_first_run_guidance,
            )

            if is_interactive_tty():
                from rich.console import Console

                print_first_run_guidance(Console())
            else:
                skipped_names = ", ".join(st.name for st in skipped_tools)
                logger.console_output(
                    f"All tools were skipped ({len(skipped_tools)}): {skipped_names}",
                )
        else:
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
                "All selected tools are configured as post-checks - skipping main phase"
            ),
        )

    # Print main header with output directory information
    logger.print_lintro_header()

    # Announce dry-run mode so users know no files will be modified.
    if dry_run_preview and output_format.lower() not in {"json", "sarif"}:
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

    # Resolve the git-diff base ref (if --diff was supplied). Non-git dirs and
    # unresolvable default refs fall back to a full scan with a warning; an
    # explicit but unresolvable ref is a hard error. Scan targets may span
    # multiple repositories; each repo's diff is computed independently.
    resolved_diff_base: str | None = None
    if diff_base is not None:
        from lintro.utils.git_diff import (
            DIFF_DEFAULT_SENTINEL,
            DiffResolutionError,
            all_repo_defaults_resolvable,
            get_changed_files_for_paths,
            is_git_repository,
            resolve_git_cwd_from_paths,
        )

        repo_groups = resolve_git_cwd_from_paths(paths)
        has_repo_paths = any(root is not None for root in repo_groups)

        if not has_repo_paths and not is_git_repository():
            logger.console_output(
                text="--diff requested but not inside a git repository; "
                "scanning all files.",
                color="yellow",
            )
        elif diff_base == DIFF_DEFAULT_SENTINEL:
            if all_repo_defaults_resolvable(paths):
                resolved_diff_base = DIFF_DEFAULT_SENTINEL
            else:
                logger.console_output(
                    text="--diff: could not resolve a default base ref in every "
                    "repository (tried origin/HEAD, origin/main, main, ...); "
                    "scanning all files.",
                    color="yellow",
                )
        else:
            resolved_diff_base = diff_base

        if resolved_diff_base is not None:
            try:
                changed = get_changed_files_for_paths(resolved_diff_base, paths)
            except DiffResolutionError as exc:
                logger.console_output(text=f"Error: {exc}", color="red")
                return 1
            # Non-repo scan targets are scanned in full (they have no diff to
            # filter against), but the changed-file count only covers the
            # repository targets. Warn so the count below isn't read as the
            # whole scan scope when targets are mixed (#1618).
            non_repo_targets = repo_groups.get(None)
            if non_repo_targets and has_repo_paths:
                logger.console_output(
                    text=(
                        f"--diff: {len(non_repo_targets)} scan target(s) are "
                        "outside a git repository and are scanned in full (not "
                        "diff-filtered); the changed-file count below counts only "
                        "the repository target(s)."
                    ),
                    color="yellow",
                )
            display_base = (
                "default base"
                if resolved_diff_base == DIFF_DEFAULT_SENTINEL
                else resolved_diff_base
            )
            logger.console_output(
                text=(
                    f"Diff mode: scanning {len(changed)} file(s) changed vs "
                    f"{display_base}"
                ),
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

    # Pre-execution config summary. Suppressed for clean-stdout formats
    # (json/sarif/csv/markdown) and score-only mode because it writes the rich
    # Configuration box to stdout via its own Console, bypassing route_stderr.
    if not clean_stdout_output and not score_only and (tools_to_run or skipped_tools):
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
            ai_config=lintro_config.ai,
        )

        # Confirmation prompt — skip when non-interactive
        import sys

        auto_continue = yes or is_ci or not sys.stdin.isatty()
        if not auto_continue:
            import click as _click

            _click.echo("Proceed? [Y/n] ", nl=False)
            try:
                answer = _click.getchar()
                _click.echo(answer)  # echo the keypress
            except (EOFError, KeyboardInterrupt):
                _click.echo()
                answer = "n"
            if answer.lower() == "n":
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
            max_fix_retries=lintro_config.execution.max_fix_retries,
            diff_base=resolved_diff_base,
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
        if dry_run_preview:
            all_results = [_filter_result_to_fixable(r) for r in all_results]

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

            _display_fix_result(
                result,
                output_format=output_format,
                raw_output=raw_output,
                console_output_func=logger.console_output,
                success_func=success_func,
                action=action,
            )

    else:
        # Sequential execution (original behavior)
        for tool_name in tools_to_run:
            try:
                tool = tool_manager.get_tool(tool_name)
                display_name = get_tool_display_name(tool_name)

                # Print tool header before execution
                logger.print_tool_header(tool_name=display_name, action=action)

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
                    action=action,
                    post_tools=post_tools_early,
                    auto_install=effective_auto_install,
                    lintro_config=lintro_config,
                    diff_base=resolved_diff_base,
                )

                # Execute the tool
                if action == Action.FIX:
                    result = _run_fix_with_retry(
                        tool=tool,
                        paths=paths,
                        options={},
                        max_retries=lintro_config.execution.max_fix_retries,
                    )
                else:
                    result = tool.check(paths, {})

                # Populate doc_url on each issue from the plugin
                _enrich_issues_with_doc_urls(tool, result)

                # Dry-run: restrict to issues a real fmt would actually fix so
                # the displayed tables, counts, and exit code exclude
                # non-auto-fixable check-mode diagnostics (e.g. ruff lint rules
                # that are not --fix-able).
                if dry_run_preview:
                    result = _filter_result_to_fixable(result)

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

                # Display the result (with initial issue details in fix mode)
                _display_fix_result(
                    result,
                    output_format=output_format,
                    raw_output=raw_output,
                    console_output_func=logger.console_output,
                    success_func=success_func,
                    action=action,
                )

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
        diff_base=resolved_diff_base,
    )

    # Dry-run: post-checks may append additional check-mode results. Restrict
    # every result to its would-fix subset and re-derive the totals so the
    # summary and exit code count only auto-fixable issues.
    if dry_run_preview:
        all_results[:] = [
            r if getattr(r, "skipped", False) else _filter_result_to_fixable(r)
            for r in all_results
        ]
        total_issues, total_fixed, total_remaining = aggregate_tool_results(
            all_results,
            action,
        )

    # AI enhancement via hook pattern
    effective_ai_fix = ai_fix or lintro_config.ai.default_fix
    _warn_ai_fix_disabled(
        action=action,
        ai_fix=effective_ai_fix,
        ai_lint_enabled=lintro_config.ai.lint_enabled,
        logger=logger,
        output_format=output_format,
    )

    from lintro.ai.hook import AIPostExecutionHook

    ai_hook = AIPostExecutionHook(
        lintro_config,
        ai_fix=effective_ai_fix,
        transport=transport,
    )
    ai_result = None
    if ai_hook.should_run(action):
        try:
            ai_result = ai_hook.execute(
                action,
                all_results,
                console_logger=logger,
                output_format=output_format,
            )
        except Exception as exc:
            from loguru import logger as loguru_logger

            loguru_logger.opt(exception=True).debug(f"AI hook failed: {exc}")
            if getattr(lintro_config.ai, "fail_on_ai_error", False):
                raise
            if output_format.lower() not in ("json", "sarif"):
                logger.console_output(f"Warning: AI enhancement failed: {exc}")
            from lintro.ai.models import AIResult

            ai_result = AIResult(error=True, message=str(exc))
        if ai_result is not None:
            total_issues, total_fixed, total_remaining = aggregate_tool_results(
                all_results,
                action,
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

    # AI-driven exit code adjustments
    if ai_result is not None:
        ai_config = lintro_config.ai
        if ai_config.fail_on_unfixed and ai_result.unfixed_issues > 0:
            final_exit_code = 1
        if ai_config.fail_on_ai_error and ai_result.error:
            final_exit_code = 1

    # Compute the deterministic 0-100 health score from the aggregated results.
    from lintro.utils.health_score import health_score_for_results

    health = health_score_for_results(
        all_results,
        getattr(lintro_config, "score", None),
    )

    # CI gate: fail the run when the score falls below the requested threshold.
    if fail_under is not None and health.score < fail_under:
        final_exit_code = 1

    # Display results
    if all_results:
        if score_only:
            # Score-only wins over JSON/SARIF so stdout stays a bare number.
            print(health.score)
        elif output_format.lower() == "json":
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
                health_score=health.to_dict(),
            )
            print(json.dumps(json_data, indent=2))
        elif output_format.lower() == "sarif":
            from lintro.ai.output.sarif import render_fixes_sarif
            from lintro.ai.output.sarif_bridge import (
                standard_issues_from_results,
                suggestions_from_results,
                summary_from_results,
            )
            from lintro.utils.output.file_writer import build_doc_url_map

            suggestions = suggestions_from_results(all_results)
            summary = summary_from_results(all_results)
            standard_issues = standard_issues_from_results(all_results)
            sarif_json = render_fixes_sarif(
                suggestions,
                summary,
                doc_urls=build_doc_url_map(all_results) or None,
                standard_issues=standard_issues,
            )
            print(sarif_json)
        elif output_format.lower() == "csv":
            # Emit a single clean CSV document on stdout; decorative UI has been
            # routed to stderr so stdout parses with csv.reader.
            from lintro.utils.output.file_writer import render_csv_report

            # Emitted verbatim as UTF-8 bytes so the csv module's \r\n line
            # terminators are not translated a second time on Windows and the
            # payload stays byte-identical to the --output file artifact.
            _write_stdout_verbatim(render_csv_report(all_results))
        elif output_format.lower() == "markdown":
            # Emit a single clean Markdown report on stdout.
            from lintro.utils.output.file_writer import render_markdown_report

            print(render_markdown_report(all_results, action))
        else:
            logger.print_execution_summary(action, all_results)

            # Dry-run summary: state clearly what a real fmt run would fix.
            if dry_run_preview:
                from lintro.utils.summary_tables import count_affected_files

                file_count = count_affected_files(all_results)
                if total_issues > 0:
                    logger.console_output(
                        text=(
                            f"Would fix {total_issues} "
                            f"issue{'s' if total_issues != 1 else ''} in "
                            f"{file_count} file{'s' if file_count != 1 else ''}"
                        ),
                        color="cyan",
                    )
                else:
                    logger.console_output(
                        text="Nothing to fix - no auto-fixable issues found",
                        color="green",
                    )

            # Always-on health score line at the end of a check run.
            if action == Action.CHECK:
                _tier_color = {
                    "great": "green",
                    "needs-work": "yellow",
                    "critical": "red",
                }.get(health.tier.label, "cyan")
                _tier_label = health.tier.label
                logger.console_output(
                    text=f"Health score: {health.score}/100 ({_tier_label})",
                    color=_tier_color,
                )

        # Route warnings to stderr (loguru) for clean-stdout formats so
        # plain-text messages don't corrupt the JSON/SARIF/CSV/Markdown
        # document on stdout.
        _is_machine = clean_stdout_output

        def _warn(msg: str) -> None:
            if _is_machine:
                from loguru import logger as loguru_logger

                loguru_logger.warning(msg)
            else:
                logger.console_output(msg)

        # Capture the raw console buffer so report.md mirrors the terminal
        # output and downstream consumers (PR comment job, fail-on-lint) can
        # read console.log from the run directory.
        console_text: str | None = None
        if not _is_machine:
            get_buffer = getattr(logger, "get_buffer", None)
            if callable(get_buffer):
                buffered = get_buffer()
                if isinstance(buffered, str):
                    console_text = buffered
            if console_text is not None:
                try:
                    output_manager.write_console_log(content=console_text)
                except OSError as e:
                    _warn(f"Warning: Failed to write console.log: {e}")

        # Write report files (markdown, html, csv)
        try:
            output_manager.write_reports_from_results(
                all_results,
                console_text=console_text,
            )
        except (OSError, ValueError, TypeError) as e:
            _warn(f"Warning: Failed to write reports: {e}")
            # Continue execution - report writing failures should not stop the tool

        # Write user-specified output file (--output flag)
        if output_file is not None:
            try:
                from lintro.enums.output_format import (
                    OutputFormat,
                    normalize_output_format,
                )
                from lintro.utils.output.file_writer import write_output_file

                fmt = normalize_output_format(output_format)
                if fmt == OutputFormat.SARIF:
                    from pathlib import Path

                    from lintro.ai.output.sarif import write_sarif
                    from lintro.ai.output.sarif_bridge import (
                        standard_issues_from_results,
                        suggestions_from_results,
                        summary_from_results,
                    )
                    from lintro.utils.output.file_writer import build_doc_url_map

                    suggestions = suggestions_from_results(all_results)
                    summary = summary_from_results(all_results)
                    standard_issues = standard_issues_from_results(all_results)
                    write_sarif(
                        suggestions,
                        summary,
                        output_path=Path(output_file),
                        doc_urls=build_doc_url_map(all_results) or None,
                        standard_issues=standard_issues,
                    )
                else:
                    write_output_file(
                        output_path=output_file,
                        output_format=fmt,
                        all_results=all_results,
                        action=action,
                        total_issues=total_issues,
                        total_fixed=total_fixed,
                    )
            except (OSError, ValueError, TypeError) as e:
                _warn(f"Warning: Failed to write output file: {e}")

        # Write side-channel artifact files when configured or when
        # running inside GitHub Actions (SARIF auto-emit for Code Scanning).
        _write_artifacts(
            all_results,
            lintro_config,
            logger,
            action=action,
            total_issues=total_issues,
            total_fixed=total_fixed,
            warn_func=_warn,
        )

        # Clean up old run directories to prevent unbounded growth
        try:
            output_manager.cleanup_old_runs()
        except OSError as e:
            _warn(f"Warning: Failed to clean up old runs: {e}")

    elif score_only:
        # Empty result set (e.g. all tools skipped) still needs numeric stdout.
        print(health.score)

    return final_exit_code
