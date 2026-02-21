"""Post-execution hook for AI enhancement.

Replaces inline ``if lintro_config.ai.enabled:`` checks in tool_executor
with a structured hook pattern. AI stays auto-invoked after check/format
-- no standalone command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lintro.enums.action import Action

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.console.logger import ThreadSafeConsoleLogger


class AIPostExecutionHook:
    """Hook that runs AI enhancement after tool execution.

    Attributes:
        _lintro_config: Full Lintro config with AI section.
        _ai_fix: Whether AI fix was requested (CLI flag or config).
    """

    def __init__(
        self,
        lintro_config: LintroConfig,
        *,
        ai_fix: bool = False,
    ) -> None:
        """Initialize the hook.

        Args:
            lintro_config: Full Lintro configuration.
            ai_fix: Whether AI fix suggestions were requested.
        """
        self._lintro_config = lintro_config
        self._ai_fix = ai_fix or lintro_config.ai.default_fix

    def should_run(self, action: Action) -> bool:
        """Check whether AI enhancement should run for this action.

        Args:
            action: The action being performed (CHECK, FIX, TEST).

        Returns:
            True if AI is enabled and action is CHECK or FIX.
        """
        return self._lintro_config.ai.enabled and action in {
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
    ) -> None:
        """Run AI enhancement on tool results.

        Args:
            action: The action that was performed.
            all_results: Results from all tools.
            console_logger: Logger for console output.
            output_format: Output format string.
        """
        try:
            from lintro.ai.orchestrator import run_ai_enhancement

            run_ai_enhancement(
                action=action,
                all_results=all_results,
                lintro_config=self._lintro_config,
                logger=console_logger,
                output_format=output_format,
                ai_fix=self._ai_fix,
            )
        except Exception as e:
            logger.debug(f"AI post-execution hook failed: {e}", exc_info=True)
