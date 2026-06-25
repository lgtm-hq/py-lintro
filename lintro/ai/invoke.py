"""Unified AI invocation with retry, fallback, and budget tracking."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lintro.ai.budget import CostBudget
from lintro.ai.fallback import complete_with_fallback
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.response import AIResponse
from lintro.ai.retry import with_retry

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider

__all__ = ["call_ai"]


def call_ai(
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

    Returns:
        The provider response with usage metadata.
    """
    tokens = max_tokens if max_tokens is not None else ai_config.max_tokens

    def _call_once() -> AIResponse:
        return complete_with_fallback(
            provider,
            user_prompt,
            fallback_models=list(ai_config.fallback_models),
            system=system_prompt,
            max_tokens=tokens,
            timeout=ai_config.api_timeout,
            repo_root=repo_root,
            use_one_shot=use_one_shot,
            cli_schema=cli_schema,
        )

    def _budgeted_call() -> AIResponse:
        if budget is not None and budget.max_cost_usd is not None:
            return budget.execute(
                _call_once,
                cost_of=lambda response: response.cost_estimate,
            )
        response = _call_once()
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    call_with_retry = with_retry(
        max_retries=ai_config.max_retries,
        base_delay=ai_config.retry_base_delay,
        max_delay=ai_config.retry_max_delay,
        backoff_factor=ai_config.retry_backoff_factor,
    )(_budgeted_call)

    return cast(AIResponse, call_with_retry())
