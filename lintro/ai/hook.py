"""Post-execution hook for AI enhancement.

Replaces inline ``if lintro_config.ai.enabled:`` checks in tool_executor
with a structured hook pattern. AI stays auto-invoked after check/fix
-- no standalone command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.models import AIResult
from lintro.enums.action import Action

if TYPE_CHECKING:
    from lintro.ai.resolved_ai_config import ResolvedAIConfig
    from lintro.config.lintro_config import LintroConfig
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.console.logger import ThreadSafeConsoleLogger


class AIPostExecutionHook:
    """Hook that runs AI enhancement after tool execution."""

    def __init__(
        self,
        lintro_config: LintroConfig,
        *,
        resolved_ai_config: ResolvedAIConfig | None = None,
        ai_fix: bool = False,
    ) -> None:
        """Initialize the hook.

        Args:
            lintro_config: Full Lintro configuration.
            resolved_ai_config: Effective AI config plus provenance, already
                resolved for this invocation (CLI overlays included) so the
                raw ``ai:`` mapping is resolved once per run. Resolved here
                without overlays when omitted.
            ai_fix: Whether AI fix suggestions were requested.
        """
        from lintro.ai.interface import resolve_effective_ai_config

        self._lintro_config = lintro_config
        self._resolved_ai_config = (
            resolved_ai_config
            if resolved_ai_config is not None
            else resolve_effective_ai_config(lintro_config)
        )
        self._ai_config = self._resolved_ai_config.config
        self._ai_fix = ai_fix

    def should_run(self, action: Action) -> bool:
        """Check whether AI enhancement should run for this action.

        Args:
            action: The action being performed (CHECK, FIX, TEST).

        Returns:
            True if AI lint summarization is enabled and action is CHECK or
            FIX.
        """
        return self._ai_config.lint_enabled and action in {
            Action.CHECK,
            Action.FIX,
        }

    def execute(
        self,
        action: Action,
        all_results: list[ToolResult],
        *,
        console_logger: ThreadSafeConsoleLogger,
        output_format: str,
    ) -> AIResult:
        """Run AI enhancement on tool results.

        Args:
            action: The action that was performed.
            all_results: Results from all tools.
            console_logger: Logger for console output.
            output_format: Output format string.

        Returns:
            AIResult with structured outcome data.

        Raises:
            Exception: Re-raised when ``fail_on_ai_error`` is enabled.
        """
        try:
            from lintro.ai.orchestrator import run_ai_enhancement

            return run_ai_enhancement(
                action=action,
                all_results=all_results,
                lintro_config=self._lintro_config,
                ai_config=self._resolved_ai_config,
                logger=console_logger,
                output_format=output_format,
                ai_fix=self._ai_fix,
            )
        except Exception as e:
            logger.opt(exception=True).debug(f"AI post-execution hook failed: {e}")
            if self._ai_config.fail_on_ai_error:
                raise
            if output_format.lower() not in ("json", "sarif"):
                console_logger.warning(f"AI enhancement unavailable: {e}")
            return AIResult(error=True)
