"""Render phase of a Lintro run.

Everything that turns a :class:`~lintro.models.core.run_artifact.RunArtifact`
into output lives here: the stdout document for each output format, the
per-tool result tables streamed during execution, the run's report files, the
user-requested ``--output`` file, and the side-channel artifact files.

Splitting this out of ``run_lint_tools_simple`` (issue #1823) means adding an
output format no longer requires touching the executor, and the execute phase
performs no printing or file writing of its own.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from lintro.enums.action import Action
from lintro.models.core.sarif_enrichment import AISarifEnrichment

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.models.core.run_artifact import RunArtifact
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.execution.run_context import RunContext

__all__ = [
    "make_result_display",
    "render_needs_ai_enrichment",
    "render_run",
]

# Filenames used for each side-channel artifact format.
_ARTIFACT_EXTENSIONS: dict[str, str] = {
    "json": "results.json",
    "csv": "results.csv",
    "markdown": "results.md",
    "html": "results.html",
    "sarif": "results.sarif.json",
    "plain": "results.txt",
}


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


def _display_fix_result(
    result: ToolResult,
    *,
    output_format: str,
    raw_output: bool,
    console_output_func: Callable[..., None],
    success_func: Callable[..., None],
    action: Action,
    group_by: str = "auto",
) -> None:
    """Display a single tool result, with initial issue details when available.

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
        group_by: How to group issues in formatted output.
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
            metadata=result.metadata,
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
            group_by=group_by,
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
            metadata=result.metadata,
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
            metadata=result.metadata,
            parse_failures_count=result.parse_failures_count or 0,
        )


def make_result_display(
    *,
    logger: Any,
    output_format: str,
    raw_output: bool,
    action: Action,
    group_by: str = "auto",
) -> Callable[[ToolResult], None]:
    """Build the per-tool display callback the execute phase streams through.

    The execute phase owns no formatting of its own; it calls back into this
    closure as each tool finishes so results still appear live rather than
    only after the whole run (issue #1823).

    Args:
        logger: Console logger used for output.
        output_format: Output format for formatting issues.
        raw_output: Whether to show raw tool output.
        action: The action being performed.
        group_by: How to group issues in formatted output.

    Returns:
        Callable[[ToolResult], None]: Callback that renders one tool result.
    """

    def _success(message: str) -> None:
        logger.console_output(text=message, color="green")

    def _display(result: ToolResult) -> None:
        _display_fix_result(
            result,
            output_format=output_format,
            raw_output=raw_output,
            console_output_func=logger.console_output,
            success_func=_success,
            action=action,
            group_by=group_by,
        )

    return _display


def _write_artifacts(
    all_results: list[ToolResult],
    lintro_config: Any,
    logger: Any,
    action: Action,
    total_issues: int,
    total_fixed: int,
    *,
    warn_func: Any = None,
    ai_enrichment: AISarifEnrichment | None = None,
    profile_data: dict[str, Any] | None = None,
) -> None:
    """Write side-channel artifact files alongside primary output.

    Emits artifact files into ``.lintro/artifacts/<format>/`` for each
    format listed in ``execution.artifacts``.  SARIF (for Code Scanning) and
    JSON (for structured CI evidence, including per-tool ``timed_out`` state)
    are also auto-emitted when ``GITHUB_ACTIONS=true`` is detected, landing at
    ``.lintro/artifacts/sarif/results.sarif.json`` and
    ``.lintro/artifacts/json/results.json`` respectively.

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
        ai_enrichment: Optional AI enrichment for a SARIF artifact, supplied by
            the caller. Applied only when SARIF is actually among the
            artifacts, so a non-SARIF run never carries AI data.
        profile_data: Optional ``--profile`` payload. Attached to the JSON
            artifact only, so the artifact matches the stdout JSON document.
    """
    import os
    from pathlib import Path

    from lintro.enums.output_format import OutputFormat, normalize_output_format
    from lintro.utils.output.file_writer import write_output_file

    artifacts: list[str] = [a.lower() for a in lintro_config.execution.artifacts]
    is_gha = os.environ.get("GITHUB_ACTIONS") == "true"

    # Auto-emit SARIF in GitHub Actions for Code Scanning integration.
    if is_gha and "sarif" not in artifacts:
        artifacts.append("sarif")

    # Auto-emit the JSON report in GitHub Actions too. SARIF omits clean tools
    # and omits failures that produced no issues, so a CI consumer cannot use
    # it to tell a tool timeout from a genuine finding without failing open.
    # The JSON report covers every tool and carries the ``timed_out`` flag,
    # and emitting it here keeps ``--output-format`` (and therefore the
    # console/grid output every existing consumer parses) untouched.
    if is_gha and "json" not in artifacts:
        artifacts.append("json")

    if not artifacts:
        return

    _emit = warn_func if warn_func is not None else logger.console_output

    enrichment = ai_enrichment if "sarif" in artifacts else None

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
                ai_enrichment=enrichment,
                profile_data=(profile_data if fmt == OutputFormat.JSON else None),
            )
        except (OSError, ValueError, TypeError) as e:
            _emit(f"Warning: Failed to write {artifact} artifact: {e}")


def render_needs_ai_enrichment(
    *,
    output_format: str,
    lintro_config: Any,
) -> bool:
    """Whether this render will emit SARIF and therefore wants AI enrichment.

    Callers use this to avoid reconstructing AI objects (and importing the AI
    layer's SARIF bridge) on runs that never produce SARIF. Both the stdout
    document and the ``--output`` file follow ``output_format``, so a single
    format check covers them.

    Args:
        output_format: The requested stdout output format.
        lintro_config: Loaded Lintro configuration, consulted for the
            configured side-channel artifact formats.

    Returns:
        bool: ``True`` when at least one SARIF document will be produced.
    """
    import os

    if output_format.lower() == "sarif":
        return True
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    artifacts = (
        getattr(getattr(lintro_config, "execution", None), "artifacts", []) or []
    )
    return any(str(a).lower() == "sarif" for a in artifacts)


def _render_stdout_document(
    artifact: RunArtifact,
    *,
    ctx: RunContext,
    output_format: str,
    ai_enrichment: AISarifEnrichment | None,
) -> None:
    """Emit the run's primary document on stdout for the requested format.

    Args:
        artifact: The completed run artifact.
        ctx: Shared run context (logger, output-mode flags).
        output_format: The requested output format.
        ai_enrichment: Optional AI enrichment for the SARIF renderer.
    """
    all_results = artifact.tool_results
    fmt = output_format.lower()

    if fmt == "json":
        import json

        from lintro.utils.json_output import create_json_output

        json_data = create_json_output(
            action=str(artifact.action),
            results=all_results,
            total_issues=artifact.total_issues,
            total_fixed=artifact.total_fixed,
            total_remaining=artifact.total_remaining,
            exit_code=artifact.exit_code,
            health_score=artifact.health.to_dict() if artifact.health else None,
        )
        if ctx.profile:
            from lintro.profiling.report import build_profile_data

            json_data["profile"] = build_profile_data(all_results)
        print(json.dumps(json_data, indent=2))
        return

    if fmt == "sarif":
        from lintro.utils.output.file_writer import build_doc_url_map
        from lintro.utils.output.sarif import (
            render_fixes_sarif,
            standard_issues_from_results,
        )

        enrichment = ai_enrichment or AISarifEnrichment()
        print(
            render_fixes_sarif(
                standard_issues_from_results(all_results),
                doc_urls=build_doc_url_map(all_results) or None,
                ai_suggestions=enrichment.suggestions,
                ai_summary=enrichment.summary,
            ),
        )
        return

    if fmt == "csv":
        # Emit a single clean CSV document on stdout; decorative UI has been
        # routed to stderr so stdout parses with csv.reader. Emitted verbatim
        # as UTF-8 bytes so the csv module's \r\n line terminators are not
        # translated a second time on Windows and the payload stays
        # byte-identical to the --output file artifact.
        from lintro.utils.output.file_writer import render_csv_report

        _write_stdout_verbatim(render_csv_report(all_results))
        return

    if fmt == "markdown":
        from lintro.utils.output.file_writer import render_markdown_report

        print(render_markdown_report(all_results, artifact.action))
        return

    _render_console_summary(artifact, ctx=ctx)


def _render_console_summary(artifact: RunArtifact, *, ctx: RunContext) -> None:
    """Print the human-readable execution summary and its trailing lines.

    Args:
        artifact: The completed run artifact.
        ctx: Shared run context supplying the console logger.
    """
    logger = ctx.logger
    logger.print_execution_summary(artifact.action, artifact.tool_results)

    if ctx.profile:
        from lintro.profiling.report import render_profile_report

        profile_report = render_profile_report(artifact.tool_results)
        if profile_report:
            logger.console_output(text="")
            logger.console_output(text=profile_report)

    # Dry-run summary: state clearly what a real fmt run would fix.
    if artifact.dry_run_preview:
        from lintro.utils.summary_tables import count_affected_files

        file_count = count_affected_files(artifact.tool_results)
        total_issues = artifact.total_issues
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
    if artifact.action == Action.CHECK and artifact.health is not None:
        health = artifact.health
        tier_color = {
            "great": "green",
            "needs-work": "yellow",
            "critical": "red",
        }.get(health.tier.label, "cyan")
        logger.console_output(
            text=f"Health score: {health.score}/100 ({health.tier.label})",
            color=tier_color,
        )


def _write_run_files(
    artifact: RunArtifact,
    *,
    ctx: RunContext,
    output_format: str,
    output_file: str | None,
    ai_enrichment: AISarifEnrichment | None,
    warn_func: Callable[[str], None],
) -> None:
    """Write the run directory's reports, the ``--output`` file, and artifacts.

    Args:
        artifact: The completed run artifact.
        ctx: Shared run context (logger, output manager, config).
        output_format: The requested output format.
        output_file: Path passed to ``--output``, or ``None``.
        ai_enrichment: Optional AI enrichment for SARIF outputs.
        warn_func: Callback used to report non-fatal write failures.
    """
    all_results = artifact.tool_results
    output_manager = ctx.output_manager

    # Capture the raw console buffer so report.md mirrors the terminal output
    # and downstream consumers (PR comment job, fail-on-lint) can read
    # console.log from the run directory.
    console_text: str | None = None
    if not ctx.clean_stdout_output:
        get_buffer = getattr(ctx.logger, "get_buffer", None)
        if callable(get_buffer):
            buffered = get_buffer()
            if isinstance(buffered, str):
                console_text = buffered
        if console_text is not None:
            try:
                output_manager.write_console_log(content=console_text)
            except OSError as e:
                warn_func(f"Warning: Failed to write console.log: {e}")

    # Write report files (markdown, html, csv)
    try:
        output_manager.write_reports_from_results(
            all_results,
            console_text=console_text,
        )
    except (OSError, ValueError, TypeError) as e:
        warn_func(f"Warning: Failed to write reports: {e}")
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

                from lintro.utils.output.file_writer import build_doc_url_map
                from lintro.utils.output.sarif import (
                    standard_issues_from_results,
                    write_sarif,
                )

                enrichment = ai_enrichment or AISarifEnrichment()
                write_sarif(
                    standard_issues_from_results(all_results),
                    output_path=Path(output_file),
                    doc_urls=build_doc_url_map(all_results) or None,
                    ai_suggestions=enrichment.suggestions,
                    ai_summary=enrichment.summary,
                )
            else:
                file_profile = None
                if ctx.profile and fmt == OutputFormat.JSON:
                    from lintro.profiling.report import build_profile_data

                    file_profile = build_profile_data(all_results)
                write_output_file(
                    output_path=output_file,
                    output_format=fmt,
                    all_results=all_results,
                    action=artifact.action,
                    total_issues=artifact.total_issues,
                    total_fixed=artifact.total_fixed,
                    profile_data=file_profile,
                )
        except (OSError, ValueError, TypeError) as e:
            warn_func(f"Warning: Failed to write output file: {e}")

    # Write side-channel artifact files when configured or when running inside
    # GitHub Actions (SARIF auto-emit for Code Scanning).
    artifact_profile = None
    if ctx.profile:
        from lintro.profiling.report import build_profile_data

        artifact_profile = build_profile_data(all_results)

    _write_artifacts(
        all_results,
        ctx.lintro_config,
        ctx.logger,
        action=artifact.action,
        total_issues=artifact.total_issues,
        total_fixed=artifact.total_fixed,
        warn_func=warn_func,
        ai_enrichment=ai_enrichment,
        profile_data=artifact_profile,
    )

    # Clean up old run directories to prevent unbounded growth
    try:
        output_manager.cleanup_old_runs()
    except OSError as e:
        warn_func(f"Warning: Failed to clean up old runs: {e}")


def render_run(
    artifact: RunArtifact,
    *,
    ctx: RunContext,
    output_format: str,
    output_file: str | None = None,
    ai_enrichment: AISarifEnrichment | None = None,
) -> None:
    """Turn a completed :class:`RunArtifact` into output.

    Emits the primary document on stdout in the requested format, then writes
    the run directory's reports, any ``--output`` file, and the configured
    side-channel artifacts. Runs that ended before executing anything
    (``artifact.early_exit``) render nothing: the execute phase already printed
    the diagnostic.

    Args:
        artifact: The completed run artifact to render.
        ctx: Shared run context (logger, output manager, config, mode flags).
        output_format: Output format for the stdout document.
        output_file: Optional path passed to ``--output``.
        ai_enrichment: Optional AI enrichment folded into SARIF output. Callers
            that never produce SARIF may leave this ``None``; see
            :func:`render_needs_ai_enrichment`.
    """
    if artifact.early_exit:
        return

    health_score = artifact.health_score

    if not artifact.tool_results:
        # Empty result set (e.g. all tools skipped) still needs numeric stdout.
        if ctx.score_only:
            print(health_score)
        return

    from lintro.enums.group_by import GroupBy, normalize_group_by
    from lintro.utils.issue_category import enrich_tool_results_with_categories

    if normalize_group_by(ctx.group_by) == GroupBy.CATEGORY:
        enrich_tool_results_with_categories(artifact.tool_results)

    if ctx.score_only:
        # Score-only wins over JSON/SARIF so stdout stays a bare number.
        print(health_score)
    else:
        _render_stdout_document(
            artifact,
            ctx=ctx,
            output_format=output_format,
            ai_enrichment=ai_enrichment,
        )

    # Route warnings to stderr (loguru) for clean-stdout formats so plain-text
    # messages don't corrupt the JSON/SARIF/CSV/Markdown document on stdout.
    is_machine = ctx.clean_stdout_output

    def _warn(msg: str) -> None:
        if is_machine:
            from loguru import logger as loguru_logger

            loguru_logger.warning(msg)
        else:
            ctx.logger.console_output(msg)

    _write_run_files(
        artifact,
        ctx=ctx,
        output_format=output_format,
        output_file=output_file,
        ai_enrichment=ai_enrichment,
        warn_func=_warn,
    )
