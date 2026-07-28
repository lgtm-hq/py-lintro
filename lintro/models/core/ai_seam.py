"""Core-side seam types for the optional AI layer.

The core runner (:mod:`lintro.utils.tool_executor`) must not import
:mod:`lintro.ai`. Instead, callers that want AI enhancement inject the
callables declared here, and the runner consumes the small core-owned
:class:`AIOutcome` value they return. See issue #724.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig
    from lintro.enums.action import Action
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.console.logger import ThreadSafeConsoleLogger


@dataclass(frozen=True)
class AIOutcome:
    """Result of an AI layer invocation, expressed in core-only terms.

    Attributes:
        ran: Whether the AI layer actually executed for this action. When
            True the runner re-aggregates tool results, because the AI
            layer may have mutated them in place.
        force_failure: Whether AI outcomes force a non-zero exit code.
    """

    ran: bool = False
    force_failure: bool = False


class AIRunner(Protocol):
    """Callable that runs the AI layer after tool execution."""

    def __call__(
        self,
        *,
        action: Action,
        all_results: list[ToolResult],
        lintro_config: LintroConfig,
        console_logger: ThreadSafeConsoleLogger,
        output_format: str,
        ai_fix: bool = False,
        transport: str | None = None,
    ) -> AIOutcome:
        """Run the AI layer and report its effect on the run.

        Args:
            action: The action that was performed.
            all_results: Results from all tools, possibly mutated in place.
            lintro_config: Full Lintro configuration.
            console_logger: Logger for console output.
            output_format: Output format string.
            ai_fix: Whether AI fix suggestions were requested via CLI.
            transport: Optional CLI override for ``ai.transport``.

        Returns:
            The :class:`AIOutcome` describing whether AI ran and whether it
            forces a failing exit code.
        """
        ...  # pragma: no cover - protocol declaration


class AIStatusRenderer(Protocol):
    """Callable that renders pre-execution AI status lines."""

    def __call__(
        self,
        *,
        ai_config: Any | None,
        is_ci: bool,
    ) -> list[str]:
        """Render the AI rows for the pre-execution configuration summary.

        Args:
            ai_config: The raw ``ai:`` mapping held by
                :class:`~lintro.config.lintro_config.LintroConfig`, or None
                when unavailable. The core runner passes it through
                untouched; parsing it into a typed AI configuration is the
                renderer's job, so core stays free of AI imports.
            is_ci: Whether the run is in a CI environment.

        Returns:
            Rich-markup lines to place in the summary table.
        """
        ...  # pragma: no cover - protocol declaration


@dataclass(frozen=True)
class AISarifEnrichment:
    """Optional AI objects to fold into a SARIF render.

    The core SARIF renderer accepts ``ai_suggestions``/``ai_summary`` keywords
    but must not know how to build them, because reconstructing them from
    tool metadata requires :mod:`lintro.ai.models`. Core therefore passes this
    value object straight through, typed as ``Any`` on both members so no AI
    type is named outside the AI layer.

    Attributes:
        suggestions: Reconstructed AI fix suggestions, empty when AI is off.
        summary: Reconstructed AI run summary, or None when absent.
    """

    suggestions: list[Any] = field(default_factory=list)
    summary: Any | None = None


class AISarifEnricher(Protocol):
    """Callable that derives SARIF AI enrichment from tool results."""

    def __call__(
        self,
        *,
        all_results: list[ToolResult],
    ) -> AISarifEnrichment:
        """Reconstruct AI enrichment from the metadata on tool results.

        Args:
            all_results: Results from all tools, carrying any AI metadata the
                AI layer attached during the run.

        Returns:
            The :class:`AISarifEnrichment` to pass to the SARIF renderer.
        """
        ...  # pragma: no cover - protocol declaration
