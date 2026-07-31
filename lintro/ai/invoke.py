"""Unified async AI invocation with retry, fallback, and budget tracking.

``call_ai`` is the single async entry point every AI product uses. Sync
callers (Click commands, tool plugins) cross the boundary with
``asyncio.run`` at their own entry point, never here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lintro.ai.budget import CostBudget
from lintro.ai.cost import estimate_cost_with_floor
from lintro.ai.fallback import complete_with_fallback
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.response import AIResponse
from lintro.ai.retry import with_retry

# Rough chars-per-token heuristic for pre-call budget reservations. Real
# tokenizers vary, but a conservative (i.e. slightly high) estimate is what
# keeps concurrent CostBudget reservations meaningful — see
# ``CostBudget.execute``'s overspend-bound note.
_CHARS_PER_TOKEN_ESTIMATE = 4

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider

__all__ = ["call_ai"]


async def call_ai(
    *,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    user_prompt: str,
    system_prompt: str | None,
    budget: CostBudget | None,
    max_tokens: int | None = None,
    repo_root: str | None = None,
    use_one_shot: bool = False,
    cli_schema: CliSchemaRequest | None = None,
    timeout: float | None = None,
) -> AIResponse:
    """Retry, fallback, and budget tracking for all AI products.

    Args:
        provider: Configured AI provider instance.
        ai_config: AI configuration (retry, timeout, fallback models).
        user_prompt: User-facing prompt text.
        system_prompt: Optional system prompt.
        budget: Optional session cost budget to record against.
        max_tokens: Per-call token cap; defaults to ``ai_config.max_tokens``.
        repo_root: Optional repository root for CLI providers.
        use_one_shot: When True, avoid durable CLI sessions.
        cli_schema: Optional native CLI JSON schema request.
        timeout: Per-call timeout override in seconds; defaults to
            ``ai_config.api_timeout``. Callers making a supplementary call
            inside an existing timeout budget pass what remains of it so the
            extra call cannot double the budgeted wall time.

    Returns:
        The provider response with usage metadata.
    """
    tokens = max_tokens if max_tokens is not None else ai_config.max_tokens
    effective_timeout = timeout if timeout is not None else ai_config.api_timeout

    async def _call_once() -> AIResponse:
        """Perform one fallback-guarded provider call.

        Returns:
            The provider response.
        """
        return await complete_with_fallback(
            provider,
            user_prompt,
            fallback_models=list(ai_config.fallback_models),
            system=system_prompt,
            max_tokens=tokens,
            timeout=effective_timeout,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            cli_schema=cli_schema,
        )

    async def _budgeted_call() -> AIResponse:
        """Perform one call, recording its cost against the budget.

        Returns:
            The provider response.
        """
        if budget is not None and budget.max_cost_usd is not None:
            input_chars = len(user_prompt) + len(system_prompt or "")
            estimate = estimate_cost_with_floor(
                provider.model_name,
                input_tokens=input_chars // _CHARS_PER_TOKEN_ESTIMATE,
                output_tokens=tokens,
            )
            return await budget.execute(
                _call_once,
                cost_of=lambda response: response.cost_estimate,
                estimate=estimate,
            )
        response = await _call_once()
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    call_with_retry = with_retry(
        max_retries=ai_config.max_retries,
        base_delay=ai_config.retry_base_delay,
        max_delay=ai_config.retry_max_delay,
        backoff_factor=ai_config.retry_backoff_factor,
    )(_budgeted_call)

    return cast(AIResponse, await call_with_retry())
