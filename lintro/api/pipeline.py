"""The AI-aware run pipeline shared by the CLI commands and the public API.

Since issue #1823 split the executor into an execute phase and a render phase,
wiring AI into a run is no longer the executor's problem: it is three ordered
calls that this module makes in one place.

1. :func:`lintro.utils.tool_executor.execute_run` produces a
   :class:`~lintro.models.core.run_artifact.RunArtifact` without touching the
   AI layer, printing a document, or writing a file.
2. :func:`lintro.ai.interface.enhance_artifact` runs the AI layer over that
   artifact and returns a refreshed one.
3. :func:`lintro.utils.execution.run_renderer.render_run` emits the output.

This module lives under :mod:`lintro.api` rather than :mod:`lintro.utils`
precisely because it *may* import :mod:`lintro.ai`; the core executor still may
not (issue #724). The three optional AI callables the executor used to accept
(``ai_runner``, ``ai_status_renderer``, ``ai_sarif_enricher``) no longer exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.utils.execution.run_renderer import (
    make_result_display,
    render_needs_ai_enrichment,
    render_run,
)
from lintro.utils.tool_executor import build_run_context, execute_run

if TYPE_CHECKING:
    from lintro.enums.action import Action
    from lintro.models.core.run_artifact import RunArtifact
    from lintro.models.core.sarif_enrichment import AISarifEnrichment
    from lintro.utils.execution.run_context import RunContext

__all__ = [
    "run_lint_artifact",
    "run_lint_with_ai",
]


def _ai_status_lines(ctx: RunContext) -> list[str] | None:
    """Render the AI rows of the pre-execution configuration summary.

    Returns ``None`` — and imports nothing from the AI display layer — when the
    summary will not be printed at all.

    Args:
        ctx: The run context, consulted for the resolved output mode.

    Returns:
        list[str] | None: Rich-markup lines for the summary's AI row, or
        ``None`` when no summary will be shown.
    """
    if ctx.clean_stdout_output or ctx.score_only:
        return None

    from lintro.ai.interface import render_ai_status
    from lintro.utils.environment import detect_ci_environment

    ci_env = detect_ci_environment()
    return render_ai_status(
        ai_config=getattr(ctx.lintro_config, "ai", None),
        is_ci=ci_env is not None and ci_env.is_ci,
    )


def _sarif_enrichment(
    *,
    ctx: RunContext,
    artifact: RunArtifact,
    output_format: str,
) -> AISarifEnrichment | None:
    """Reconstruct SARIF AI enrichment when this run will emit SARIF.

    Args:
        ctx: The run context, consulted for the configured artifact formats.
        artifact: The completed run artifact whose results carry AI metadata.
        output_format: The requested output format.

    Returns:
        AISarifEnrichment | None: The reconstructed enrichment, or ``None``
        when no SARIF is produced.
    """
    if not render_needs_ai_enrichment(
        output_format=output_format,
        lintro_config=ctx.lintro_config,
    ):
        return None

    from lintro.ai.interface import sarif_enrichment_from_results

    return sarif_enrichment_from_results(all_results=artifact.tool_results)


def _capture_fmt_checkpoint(*, ctx: RunContext, paths: list[str]) -> None:
    """Snapshot ``fmt`` targets when ``ai.checkpoint_fmt`` is enabled.

    ``lintro fmt`` rewrites files in place, so the same pre-mutation git
    checkpoint that guards AI fix batches is offered here behind an opt-in
    flag. The ref is announced on the console because, unlike the AI fix path,
    nothing else in a ``fmt`` run holds a handle to it — ``git diff <ref>`` and
    ``git checkout <ref> -- <path>`` are the user's entry points. Outside a git
    work tree nothing is captured: an in-memory snapshot would not survive the
    process, so it would buy nothing for the price of reading every target.

    This lives in the API layer rather than the executor because the executor
    may not import :mod:`lintro.ai` (issue #724).

    Args:
        ctx: The run context for this run.
        paths: Paths the run will format.
    """
    from lintro.enums.action import Action as _Action

    ai_config = getattr(ctx.lintro_config, "ai", None)
    if (
        ctx.action != _Action.FIX
        or ctx.dry_run_preview
        or ai_config is None
        or not getattr(ai_config, "checkpoint_fmt", False)
    ):
        return

    from pathlib import Path

    from lintro.ai.checkpoints import capture_checkpoint

    checkpoint = capture_checkpoint(
        paths,
        workspace_root=Path.cwd(),
        keep=ai_config.checkpoint_retention,
    )
    if checkpoint is None or ctx.clean_stdout_output or ctx.score_only:
        return
    ctx.logger.console_output(
        text=f"Captured fmt checkpoint {checkpoint.ref}",
        color="cyan",
    )


def run_lint_artifact(
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
    ai_enabled: bool = True,
) -> RunArtifact:
    """Execute a run, apply AI enhancement, render it, and return the artifact.

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
        auto_install: Whether to auto-install Node.js deps if node_modules
            is missing.
        yes: Skip confirmation prompt and proceed immediately.
        ai_fix: Enable AI fix suggestions with interactive review (check only).
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        transport: Optional CLI override for ``ai.transport`` when AI runs.
        dry_run: Preview what ``fmt`` would fix without modifying files.
        score: When True with human-readable output, print only the 0-100
            health score line and suppress the normal execution summary.
        fail_under: When set, exit with code 1 if the computed health score is
            strictly below this threshold (CI gate).
        diff_base: Git base ref for ``--diff`` scanning, or ``None``.
        no_art: When True, suppress decorative ASCII art.
        ai_enabled: Whether the post-execution AI enhancement may run.
            ``lintro test`` sets this to False because AI never applies to the
            test action. It gates :func:`~lintro.ai.interface.enhance_artifact`
            only; the AI status rows of the configuration summary and any SARIF
            enrichment are still produced, exactly as they were before the
            executor's AI seams were removed.

    Returns:
        RunArtifact: The rendered run's results, totals, health score, and
        exit code.
    """
    del stream, no_log  # accepted for signature stability; not yet implemented

    ctx = build_run_context(
        action=action,
        output_format=output_format,
        score=score,
        debug=debug,
        no_art=no_art,
        dry_run=dry_run,
    )
    _capture_fmt_checkpoint(ctx=ctx, paths=paths)
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
        ai_status_lines=_ai_status_lines(ctx),
        on_tool_result=make_result_display(
            logger=ctx.logger,
            output_format=output_format,
            raw_output=raw_output,
            action=ctx.action,
        ),
    )

    if ai_enabled:
        from lintro.ai.interface import enhance_artifact

        artifact = enhance_artifact(
            artifact,
            ctx=ctx,
            output_format=output_format,
            ai_fix=ai_fix,
            transport=transport,
            fail_under=fail_under,
        )

    render_run(
        artifact,
        ctx=ctx,
        output_format=output_format,
        output_file=output_file,
        ai_enrichment=_sarif_enrichment(
            ctx=ctx,
            artifact=artifact,
            output_format=output_format,
        ),
    )
    return artifact


# NOTE: keep this signature in lockstep with ``run_lint_artifact`` above. It is
# spelled out rather than delegated through ``*args``/ParamSpec because callers
# (and the CLI/API tests that mock this function) assert on individual keyword
# arguments, which a transparent wrapper would erase.
def run_lint_with_ai(
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
    ai_enabled: bool = True,
) -> int:
    """Run the full AI-aware pipeline and return only the process exit code.

    Exit-code-only counterpart to :func:`run_lint_artifact`, kept so the CLI
    commands and the existing public API functions keep their ``int`` contract.

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
        auto_install: Whether to auto-install Node.js deps if node_modules
            is missing.
        yes: Skip confirmation prompt and proceed immediately.
        ai_fix: Enable AI fix suggestions with interactive review (check only).
        ignore_conflicts: Whether to ignore tool configuration conflicts.
        transport: Optional CLI override for ``ai.transport`` when AI runs.
        dry_run: Preview what ``fmt`` would fix without modifying files.
        score: When True with human-readable output, print only the 0-100
            health score line and suppress the normal execution summary.
        fail_under: When set, exit with code 1 if the computed health score is
            strictly below this threshold (CI gate).
        diff_base: Git base ref for ``--diff`` scanning, or ``None``.
        no_art: When True, suppress decorative ASCII art.
        ai_enabled: Whether the post-execution AI enhancement may run.

    Returns:
        int: Exit code (0 for success, 1 for failures).
    """
    return run_lint_artifact(
        action=action,
        paths=paths,
        tools=tools,
        tool_options=tool_options,
        exclude=exclude,
        include_venv=include_venv,
        group_by=group_by,
        output_format=output_format,
        verbose=verbose,
        raw_output=raw_output,
        output_file=output_file,
        incremental=incremental,
        debug=debug,
        stream=stream,
        no_log=no_log,
        auto_install=auto_install,
        yes=yes,
        ai_fix=ai_fix,
        ignore_conflicts=ignore_conflicts,
        transport=transport,
        dry_run=dry_run,
        score=score,
        fail_under=fail_under,
        diff_base=diff_base,
        no_art=no_art,
        ai_enabled=ai_enabled,
    ).exit_code
