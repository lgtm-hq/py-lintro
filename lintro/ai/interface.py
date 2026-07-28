"""The single integration point between the core runner and the AI layer.

Core modules must not import :mod:`lintro.ai` internals. Callers that want
AI enhancement (the ``chk``/``fmt`` CLI handlers and the public Python API)
inject the callables from this module into
:func:`lintro.utils.tool_executor.run_lint_tools_simple`, which consumes the
core-owned :class:`~lintro.models.core.ai_seam.AIOutcome` they return.

The surface here is deliberately small — the goal of issue #724 is fewer
import edges, not a plugin system. All heavy AI imports are function-local so
that importing this module stays cheap on the no-AI path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from lintro.enums.action import Action
from lintro.models.core.ai_seam import AIOutcome

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.models import AIResult
    from lintro.config.lintro_config import LintroConfig
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.console.logger import ThreadSafeConsoleLogger

__all__ = [
    "ai_exit_code_override",
    "render_ai_status",
    "resolve_ai_config",
    "run_ai_layer",
]


def resolve_ai_config(lintro_config: LintroConfig) -> AIConfig:
    """Parse the raw ``ai:`` mapping on a config into an :class:`AIConfig`.

    :class:`~lintro.config.lintro_config.LintroConfig` stores the ``ai:``
    section verbatim as a mapping so that :mod:`lintro.config` never imports
    :mod:`lintro.ai` (issue #724). Every caller that needs typed AI settings
    goes through this function.

    Unknown keys are dropped with a warning, so resolving is also what makes
    a typo'd ``ai.*`` key discoverable. Callers should resolve once per run
    and pass the result down rather than re-resolving, which would repeat
    that warning.

    Args:
        lintro_config: Full Lintro configuration.

    Returns:
        The parsed AI configuration, with model defaults for omitted fields.
    """
    from lintro.ai.config import AIConfig

    return AIConfig.from_mapping(lintro_config.ai)


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
    ai_config = resolve_ai_config(lintro_config)
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
        ai_config=ai_config,
        ai_fix=effective_ai_fix,
        transport=transport,
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
    ai_config: AIConfig | Mapping[str, Any] | None,
    is_ci: bool,
) -> list[str]:
    """Render the pre-execution AI status lines.

    Args:
        ai_config: Raw ``ai:`` mapping from the config (what the core
            executor holds), an already-parsed :class:`AIConfig`, or None
            when unavailable.
        is_ci: Whether the run is in a CI environment.

    Returns:
        Rich-markup lines for the ``AI`` row of the configuration summary.
        Never empty: a disabled configuration still renders one line.
    """
    from lintro.ai.display.status import render_ai_status as _render_ai_status

    return _render_ai_status(ai_config=ai_config, is_ci=is_ci)
