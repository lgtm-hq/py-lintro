"""The single integration point between the core runner and the AI layer.

Core modules must not import :mod:`lintro.ai` internals. Since the executor
was split into an execute phase and a render phase (issue #1823), callers that
want AI enhancement — the ``chk``/``fmt`` CLI handlers and the public Python
API — simply call :func:`enhance_artifact` *between* the two phases. Nothing is
injected into core any more: the three optional callables the executor used to
accept (``ai_runner``, ``ai_status_renderer``, ``ai_sarif_enricher``) are gone,
along with the seam protocols that described them.

The surface here is deliberately small — the goal of issue #724 is fewer
import edges, not a plugin system. All heavy AI imports are function-local so
that importing this module stays cheap on the no-AI path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lintro.enums.action import Action
from lintro.models.core.sarif_enrichment import AISarifEnrichment

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.effective_config import AICliOverrides
    from lintro.ai.models import AIResult
    from lintro.ai.resolved_ai_config import ResolvedAIConfig
    from lintro.config.lintro_config import LintroConfig
    from lintro.models.core.run_artifact import RunArtifact
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.console.logger import ThreadSafeConsoleLogger
    from lintro.utils.execution.run_context import RunContext

__all__ = [
    "enhance_artifact",
    "render_ai_status",
    "resolve_ai_config",
    "resolve_effective_ai_config",
    "sarif_enrichment_from_results",
]


@dataclass(frozen=True)
class AIOutcome:
    """Result of an AI layer invocation, expressed in exit-code terms.

    Attributes:
        ran: Whether the AI layer actually executed for this action. When
            True the caller re-aggregates tool results, because the AI layer
            may have mutated them in place.
        force_failure: Whether AI outcomes force a non-zero exit code.
    """

    ran: bool = False
    force_failure: bool = False


def sarif_enrichment_from_results(
    *,
    all_results: list[ToolResult],
) -> AISarifEnrichment:
    """Reconstruct SARIF AI enrichment from the metadata on tool results.

    The render phase accepts the returned value as plain data rather than
    importing :mod:`lintro.ai.sarif_bridge` itself, which is what keeps
    :mod:`lintro.utils.tool_executor` and :mod:`lintro.utils.output` free of
    AI imports (issue #724).

    Args:
        all_results: Results from all tools, possibly carrying AI metadata.

    Returns:
        The reconstructed fix suggestions and run summary. Both are empty
        when no result carries AI metadata.
    """
    from lintro.ai.sarif_bridge import (
        suggestions_from_results,
        summary_from_results,
    )

    return AISarifEnrichment(
        suggestions=list(suggestions_from_results(all_results)),
        summary=summary_from_results(all_results),
    )


def resolve_effective_ai_config(
    lintro_config: LintroConfig,
    *,
    cli_overrides: AICliOverrides | None = None,
) -> ResolvedAIConfig:
    """Resolve the effective AI config, with provenance, for one invocation.

    :class:`~lintro.config.lintro_config.LintroConfig` stores the ``ai:``
    section verbatim as a mapping so that :mod:`lintro.config` never imports
    :mod:`lintro.ai` (issue #724). This is the seam where the mapping becomes
    typed: it forwards to
    :func:`lintro.ai.effective_config.resolve_effective_ai_config`, the one
    resolver every surface shares (#2299).

    Unknown keys are dropped with a warning, so resolving is also what makes
    a typo'd ``ai.*`` key discoverable. Callers resolve once per run and pass
    the result down rather than re-resolving, which would repeat that
    warning and risk disagreeing with what actually executed.

    Args:
        lintro_config: Full Lintro configuration.
        cli_overrides: Flags passed on this invocation, or None when the
            surface has none.

    Returns:
        Effective AI configuration plus per-field provenance.
    """
    from lintro.ai.effective_config import (
        NO_CLI_OVERRIDES,
    )
    from lintro.ai.effective_config import (
        resolve_effective_ai_config as _resolve,
    )

    return _resolve(
        lintro_config.ai,
        cli_overrides=cli_overrides if cli_overrides is not None else NO_CLI_OVERRIDES,
    )


def resolve_ai_config(lintro_config: LintroConfig) -> AIConfig:
    """Return the effective :class:`AIConfig` for a run without provenance.

    A thin unwrap of :func:`resolve_effective_ai_config` for the surfaces
    that only need the values — doctor's presence checks and the advisory
    tools. Surfaces that render provenance, or that carry CLI overrides, use
    :func:`resolve_effective_ai_config` instead.

    Args:
        lintro_config: Full Lintro configuration.

    Returns:
        The effective AI configuration, with model defaults for omitted
        fields.
    """
    return resolve_effective_ai_config(lintro_config).config


def _warn_ai_fix_disabled(
    *,
    action: Action,
    ai_fix: bool,
    ai_lint_enabled: bool,
    logger: Any,
    output_format: str = "",
) -> None:
    """Warn when users request AI fixes but AI lint is disabled in config.

    Args:
        action: The action being performed.
        ai_fix: Whether AI fixes were requested (CLI flag or config default).
        ai_lint_enabled: Whether AI lint enhancement is enabled in config.
        logger: Console logger used to emit the warning.
        output_format: Output format string; machine-readable formats are
            left untouched.
    """
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


def ai_exit_code_override(
    *,
    ai_result: AIResult | None,
    ai_config: AIConfig,
) -> bool:
    """Whether AI outcomes force a non-zero exit.

    Args:
        ai_result: Result of the AI run, or None when AI did not run.
        ai_config: Resolved AI configuration.

    Returns:
        True when ``ai.fail_on_unfixed`` or ``ai.fail_on_ai_error`` is
        configured and the corresponding AI outcome occurred.
    """
    if ai_result is None:
        return False
    if ai_config.fail_on_unfixed and ai_result.unfixed_issues > 0:
        return True
    return bool(ai_config.fail_on_ai_error and ai_result.error)


def run_ai_layer(
    *,
    action: Action,
    all_results: list[ToolResult],
    lintro_config: LintroConfig,
    console_logger: ThreadSafeConsoleLogger,
    output_format: str,
    ai_fix: bool = False,
    transport: str | None = None,
) -> AIOutcome:
    """Run AI enhancement for a completed lint run.

    Absorbs what the core executor used to do inline: resolve the effective
    ``ai_fix`` flag, warn when AI fixes were requested with AI lint disabled,
    gate on the action, run the AI post-execution hook, and translate the AI
    result into an exit-code decision.

    Args:
        action: The action that was performed (CHECK, FIX, TEST).
        all_results: Results from all tools. The AI layer may mutate these
            in place, which is why the returned ``ran`` flag matters.
        lintro_config: Full Lintro configuration.
        console_logger: Logger for console output.
        output_format: Output format string.
        ai_fix: Whether AI fix suggestions were requested via CLI.
        transport: Optional CLI override for ``ai.transport``.

    Returns:
        An :class:`AIOutcome` with ``ran`` set when the AI layer executed and
        ``force_failure`` set when AI outcomes require exit code 1.

    Raises:
        Exception: Re-raised when ``ai.fail_on_ai_error`` is enabled.
    """
    from lintro.ai.effective_config import AICliOverrides

    # The lint path's ``--transport`` is an ordinary CLI overlay on the one
    # resolver, not a post-resolution ``model_copy`` (#2299): resolving it
    # here is what gives ``check``/``fmt`` the same provenance as review.
    resolved = resolve_effective_ai_config(
        lintro_config,
        cli_overrides=AICliOverrides(transport=transport),
    )
    ai_config = resolved.config
    effective_ai_fix = ai_fix or ai_config.default_fix
    _warn_ai_fix_disabled(
        action=action,
        ai_fix=effective_ai_fix,
        ai_lint_enabled=ai_config.lint_enabled,
        logger=console_logger,
        output_format=output_format,
    )

    from lintro.ai.hook import AIPostExecutionHook

    ai_hook = AIPostExecutionHook(
        lintro_config,
        resolved_ai_config=resolved,
        ai_fix=effective_ai_fix,
    )
    if not ai_hook.should_run(action):
        return AIOutcome(ran=False, force_failure=False)

    ai_result: AIResult | None = None
    try:
        ai_result = ai_hook.execute(
            action,
            all_results,
            console_logger=console_logger,
            output_format=output_format,
        )
    except Exception as exc:
        from loguru import logger as loguru_logger

        loguru_logger.opt(exception=True).debug(f"AI hook failed: {exc}")
        if ai_config.fail_on_ai_error:
            raise
        if output_format.lower() not in ("json", "sarif"):
            console_logger.console_output(f"Warning: AI enhancement failed: {exc}")
        from lintro.ai.models import AIResult as _AIResult

        ai_result = _AIResult(error=True, message=str(exc))

    return AIOutcome(
        ran=True,
        force_failure=ai_exit_code_override(
            ai_result=ai_result,
            ai_config=ai_config,
        ),
    )


def render_ai_status(
    *,
    ai_config: AIConfig | ResolvedAIConfig | Mapping[str, Any] | None,
    is_ci: bool,
) -> list[str]:
    """Render the pre-execution AI status lines.

    This is where the raw ``ai:`` mapping the core executor holds becomes a
    resolved value: the renderer itself only ever sees typed input, so status
    cannot resolve differently from execution (#2299). Diagnostics are off
    for that resolution because the execution path already reported the
    unknown-key warning and the migration hints.

    Args:
        ai_config: Raw ``ai:`` mapping from the config (what the core
            executor holds), an already-parsed :class:`AIConfig`, a
            :class:`ResolvedAIConfig`, or None when unavailable.
        is_ci: Whether the run is in a CI environment.

    Returns:
        Rich-markup lines for the ``AI`` row of the configuration summary.
        Never empty: a disabled configuration still renders one line.
    """
    from lintro.ai.display.status import render_ai_status as _render_ai_status

    if isinstance(ai_config, Mapping):
        from lintro.ai.effective_config import resolve_effective_ai_config as _resolve

        ai_config = _resolve(ai_config, diagnostics=False)
    return _render_ai_status(ai_config=ai_config, is_ci=is_ci)


def enhance_artifact(
    artifact: RunArtifact,
    *,
    ctx: RunContext,
    output_format: str,
    ai_fix: bool = False,
    transport: str | None = None,
    fail_under: float | None = None,
) -> RunArtifact:
    """Run the AI layer over a completed execute phase and refresh the result.

    This is the whole AI story for a lint run since issue #1823: the CLI
    handlers and the public API call :func:`lintro.utils.tool_executor.
    execute_run`, hand the artifact here, then render whatever comes back. The
    executor no longer accepts an ``ai_runner`` and never imports the AI layer.

    Args:
        artifact: The artifact produced by the execute phase. Its
            ``tool_results`` may be mutated in place by the AI layer.
        ctx: Shared run context; supplies the console logger and config.
        output_format: Output format string.
        ai_fix: Whether AI fix suggestions were requested via CLI.
        transport: Optional CLI override for ``ai.transport``.
        fail_under: Health-score gate to re-apply after AI changed the results.

    Returns:
        RunArtifact: The original artifact when AI did not run or the run
        exited early, otherwise a refreshed artifact whose totals, health
        score, and exit code account for the AI layer's effect.
    """
    if artifact.early_exit:
        return artifact

    outcome = run_ai_layer(
        action=ctx.action,
        all_results=artifact.tool_results,
        lintro_config=ctx.lintro_config,
        console_logger=ctx.logger,
        output_format=output_format,
        ai_fix=ai_fix,
        transport=transport,
    )
    # ``run_ai_layer`` never forces a failure without having run, so a
    # non-running outcome leaves the artifact exactly as the executor built it.
    if not outcome.ran:
        return artifact

    from lintro.utils.tool_executor import refresh_artifact

    return refresh_artifact(
        artifact,
        ctx=ctx,
        fail_under=fail_under,
        force_failure=outcome.force_failure,
    )
