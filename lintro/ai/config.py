"""AI configuration model for Lintro.

Defines the AIConfig Pydantic model used in the ``ai:`` section of
.lintro-config.yaml. All AI features are opt-in and disabled by default.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AIConfig(BaseModel):
    """Configuration for AI-powered features.

    Attributes:
        enabled: Whether AI features are enabled globally.
        provider: AI provider to use ("anthropic" or "openai").
        model: Model identifier. None uses the provider's default.
        api_key_env: Custom environment variable name for the API key.
            Defaults to provider-specific env var (ANTHROPIC_API_KEY,
            OPENAI_API_KEY).
        default_fix: Whether to enable interactive AI fix suggestions
            by default in ``chk``. Equivalent to always passing
            ``--fix``. Defaults to False.
        auto_apply: Whether to automatically apply AI-generated fixes
            without user confirmation. Defaults to False for safety.
        max_tokens: Maximum tokens per AI request.
        max_fix_issues: Maximum number of issues to generate AI fixes
            for per run. Set higher to analyze more issues at the cost
            of additional API calls.
        max_parallel_calls: Maximum concurrent API calls when
            generating fixes in parallel.
        show_cost_estimate: Whether to display estimated cost before
            and after AI operations.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled: bool = False
    provider: str = Field(default="anthropic")
    model: str | None = None
    api_key_env: str | None = None
    default_fix: bool = False
    auto_apply: bool = False
    max_tokens: int = Field(default=4096, ge=1, le=128000)
    max_fix_issues: int = Field(default=20, ge=1)
    max_parallel_calls: int = Field(default=5, ge=1, le=20)
    show_cost_estimate: bool = True
