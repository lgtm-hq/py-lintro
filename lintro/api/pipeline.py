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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import click

from lintro.enums.action import Action
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.execution.run_renderer import (
    make_result_display,
    render_needs_ai_enrichment,
    render_run,
)
from lintro.utils.fingerprint_baseline_file import load_run_baseline
from lintro.utils.tool_executor import build_run_context, execute_run

if TYPE_CHECKING:
    from lintro.ai.resolved_ai_config import ResolvedAIConfig
    from lintro.models.core.run_artifact import RunArtifact
    from lintro.models.core.sarif_enrichment import AISarifEnrichment
    from lintro.utils.execution.run_context import RunContext

__all__ = [
    "run_lint_artifact",
    "run_lint_with_ai",
]


def _ai_config_error_message(exc: Exception) -> str:
    """Describe a rejected AI configuration for the CLI's usage error (exit 2).

    An invalid ``LINTRO_AI_*`` override already reads as one line. A value the
    ``ai:`` config section itself carries surfaces as a pydantic
    ``ValidationError``, whose text is a multi-line dump quoting the rejected
    input; it is reduced to the field path and the constraint instead (#3116).

    Args:
        exc: An :class:`~lintro.ai.exceptions.AIConfigOverrideError` or a
            pydantic ``ValidationError`` raised while resolving ``ai:``.

    Returns:
        The one-line message for :class:`click.UsageError`.
    """
    from pydantic import ValidationError

    from lintro.ai.provider_blocks import format_invalid_ai_config

    if isinstance(exc, ValidationError):
        return format_invalid_ai_config(exc)
    return str(exc)


@dataclass
class _RunAIConfig:
    """The invocation's effective ``ai:`` config, resolved at most once (#2374).

    The pre-execution status row, the ``fmt`` checkpoint hook and the post-run
    AI layer all need the effective AI settings. Each used to resolve the raw
    ``ai:`` mapping on its own, and only the AI layer applied the invocation's
    ``--transport`` flag, so the summary could report a transport the run did
    not use. This holder resolves on first use, with the invocation's CLI
    overlay, and hands every seam the same
    :class:`~lintro.ai.resolved_ai_config.ResolvedAIConfig`.

    Resolution stays lazy so a run that never reaches an AI seam (JSON output
    that exits early, ``lintro test`` with machine output) does not validate
    ``ai:`` at all, exactly as before.

    Attributes:
        mapping: The raw ``ai:`` section the run's config holds.
        transport: The invocation's ``--transport`` value, or None.
        diagnostics: Whether the one resolution may emit the unknown-key
            warning and migration hints. True when the AI layer may run, which
            is the only path that reported them before resolution was shared.
    """

    mapping: Mapping[str, Any] | None
    transport: str | None = None
    diagnostics: bool = True
    _resolved: ResolvedAIConfig | None = field(default=None, init=False, repr=False)

    def get(self) -> ResolvedAIConfig:
        """Return the effective AI config, resolving it on the first call.

        Returns:
            The invocation's effective AI config with provenance.

        Raises:
            click.UsageError: When an ``LINTRO_AI_*`` overlay, the
                ``--transport`` flag, or the ``ai:`` config section fails
                validation.
        """
        if self._resolved is None:
            from pydantic import ValidationError

            from lintro.ai.effective_config import (
                AICliOverrides,
                resolve_effective_ai_config,
            )
            from lintro.ai.exceptions import AIConfigOverrideError

            try:
                self._resolved = resolve_effective_ai_config(
                    self.mapping,
                    cli_overrides=AICliOverrides(transport=self.transport),
                    diagnostics=self.diagnostics,
                )
            except (AIConfigOverrideError, ValidationError) as exc:
                raise click.UsageError(_ai_config_error_message(exc)) from exc
        return self._resolved


def _ai_status_lines(
    ctx: RunContext,
    *,
    run_ai_config: _RunAIConfig,
) -> list[str] | None:
    """Render the AI rows of the pre-execution configuration summary.

    Returns ``None`` — and imports nothing from the AI display layer — when the
    summary will not be printed at all.

    The rows render the invocation's effective config, CLI flags included, so
    ``lintro check --transport cli`` reports ``transport: cli (flag)`` — the
    value the AI layer later runs with (#2374).

    Args:
        ctx: The run context, consulted for the resolved output mode.
        run_ai_config: The invocation's shared AI config resolution.

    Returns:
        list[str] | None: Rich-markup lines for the summary's AI row, or
        ``None`` when no summary will be shown.
    """
    if ctx.clean_stdout_output:
        return None

    from lintro.ai.interface import render_ai_status
    from lintro.utils.environment import detect_ci_environment

    ci_env = detect_ci_environment()
    return render_ai_status(
        ai_config=run_ai_config.get(),
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


def _capture_fmt_checkpoint(
    *,
    ctx: RunContext,
    paths: list[str],
    run_ai_config: _RunAIConfig | None = None,
) -> None:
    """Snapshot ``fmt`` targets when ``ai.checkpoint_fmt`` is enabled.

    ``lintro fmt`` rewrites files in place, so the same pre-mutation git
    checkpoint that guards AI fix batches is offered here behind an opt-in
    flag. The ref is announced on the console because, unlike the AI fix path,
    nothing else in a ``fmt`` run holds a handle to it — ``git diff <ref>`` and
    ``git restore --source=<ref> --worktree -- <path>`` are the entry points.
    Outside a git work tree nothing is captured: an in-memory snapshot would
    not survive the process, so it would buy nothing for the price of reading
    every target.

    This lives in the API layer rather than the executor because the executor
    may not import :mod:`lintro.ai` (issue #724).

    Args:
        ctx: The run context for this run.
        paths: Paths the run will format.
        run_ai_config: The invocation's shared AI config resolution, or None
            to resolve the ``ai:`` mapping here without CLI flags.

    An invalid ``LINTRO_AI_*`` overlay or ``ai:`` section surfaces as the
    :class:`click.UsageError` :meth:`_RunAIConfig.get` raises.

    Raises:
        click.ClickException: When checkpoint enumeration fails, before any
            formatting runs.
    """
    from lintro.enums.action import Action as _Action

    ai_config = getattr(ctx.lintro_config, "ai", None)
    if ctx.action != _Action.FIX or ctx.dry_run_preview or ai_config is None:
        return

    # LintroConfig stores the AI section as a raw mapping. Resolve it at
    # this API boundary before reading the checkpoint options as attributes.
    if isinstance(ai_config, dict):
        if run_ai_config is None:
            run_ai_config = _RunAIConfig(mapping=ai_config, diagnostics=False)
        ai_config = run_ai_config.get().config
    if not getattr(ai_config, "checkpoint_fmt", False):
        return

    from pathlib import Path

    from lintro.ai.checkpoints import CheckpointError, capture_checkpoint

    try:
        checkpoint = capture_checkpoint(
            paths,
            workspace_root=Path.cwd(),
            keep=ai_config.checkpoint_retention,
        )
    except CheckpointError as exc:
        raise click.ClickException(f"Failed to capture fmt checkpoint: {exc}") from exc
    if checkpoint is None or ctx.clean_stdout_output:
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
    diff_base: str | None = None,
    no_art: bool = False,
    profile: bool = False,
    ai_enabled: bool = True,
    baseline: str | Literal[False] | None = None,
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
        diff_base: Git base ref for ``--diff`` scanning, or ``None``.
        no_art: When True, suppress decorative ASCII art.
        profile: When True, render the per-tool timing table (human output) or
            attach a ``profile`` key (JSON). Executors always record main-tool
            timings; post-checks are omitted. csv/sarif/markdown stdout stay
            unchanged.
        ai_enabled: Whether the post-execution AI enhancement may run.
            ``lintro test`` sets this to False because AI never applies to the
            test action. It gates :func:`~lintro.ai.interface.enhance_artifact`
            only; the AI status rows of the configuration summary and any SARIF
            enrichment are still produced, exactly as they were before the
            executor's AI seams were removed.
        baseline: Fingerprint baseline path; ``None`` uses project config,
            while ``False`` explicitly disables filtering.

    Returns:
        RunArtifact: The rendered run's results, totals, severity tallies, and
        exit code. An invalid ``LINTRO_AI_*`` overlay, ``--transport``
        value or ``ai:`` config section raises :class:`click.UsageError`
        instead, from the run's one shared AI config resolution.
    """
    del stream, no_log  # accepted for signature stability; not yet implemented

    ctx = build_run_context(
        action=action,
        output_format=output_format,
        debug=debug,
        no_art=no_art,
        dry_run=dry_run,
        group_by=group_by,
        profile=profile,
    )
    acknowledgements = (
        load_run_baseline(baseline, ctx.lintro_config)
        if ctx.action != Action.TEST
        else None
    )
    # One resolution of ``ai:`` per run, shared by every AI seam below (#2374).
    run_ai_config = _RunAIConfig(
        mapping=getattr(ctx.lintro_config, "ai", None),
        transport=transport,
        diagnostics=ai_enabled,
    )
    _capture_fmt_checkpoint(ctx=ctx, paths=paths, run_ai_config=run_ai_config)
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
        diff_base=diff_base,
        ai_status_lines=_ai_status_lines(ctx, run_ai_config=run_ai_config),
        on_tool_result=(
            None
            if acknowledgements is not None
            else make_result_display(
                logger=ctx.logger,
                output_format=output_format,
                raw_output=raw_output,
                action=ctx.action,
                group_by=group_by,
            )
        ),
    )

    if ai_enabled and not artifact.early_exit:
        from lintro.ai.interface import enhance_artifact

        # Resolve (or reuse) ``ai:`` before the AI hook runs, so a bad section
        # is a usage error on its own. When no earlier seam resolved it, this
        # is after ``execute_run``, so a fmt run may already have written
        # files. Do not catch around the hook instead: anything it runs may
        # raise a ValidationError, and with ``ai.fail_on_ai_error`` an
        # unrelated one must not be reported as an invalid ``ai:`` section
        # (#3116). The hook receives this value rather than resolving again.
        artifact = enhance_artifact(
            artifact,
            ctx=ctx,
            output_format=output_format,
            ai_fix=ai_fix,
            resolved_ai_config=run_ai_config.get(),
        )

    if acknowledgements is not None:
        apply_baseline(artifact, acknowledgements)
        if not ctx.clean_stdout_output:
            display = make_result_display(
                logger=ctx.logger,
                output_format=output_format,
                raw_output=raw_output,
                action=ctx.action,
                group_by=group_by,
            )
            for result in artifact.tool_results:
                display(result)

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
    diff_base: str | None = None,
    no_art: bool = False,
    profile: bool = False,
    ai_enabled: bool = True,
    baseline: str | Literal[False] | None = None,
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
        diff_base: Git base ref for ``--diff`` scanning, or ``None``.
        no_art: When True, suppress decorative ASCII art.
        profile: When True, render the per-tool timing table (human output) or
            attach a ``profile`` key (JSON). Executors always record main-tool
            timings; post-checks are omitted. csv/sarif/markdown stdout stay
            unchanged.
        ai_enabled: Whether the post-execution AI enhancement may run.
        baseline: Fingerprint baseline path; ``None`` uses project config,
            while ``False`` explicitly disables filtering.

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
        diff_base=diff_base,
        no_art=no_art,
        profile=profile,
        ai_enabled=ai_enabled,
        baseline=baseline,
    ).exit_code
