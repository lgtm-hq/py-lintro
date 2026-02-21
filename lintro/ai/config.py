"""AI configuration model for Lintro.

Defines the AIConfig Pydantic model used in the ``ai:`` section of
.lintro-config.yaml. All AI features are opt-in and disabled by default.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        auto_apply_safe_fixes: Whether safe style-only AI fixes should
            be auto-applied in non-interactive mode. Defaults to True.
        max_tokens: Maximum tokens per AI request.
        max_fix_issues: Maximum number of issues to generate AI fixes
            for per run. Set higher to analyze more issues at the cost
            of additional API calls.
        max_parallel_calls: Maximum concurrent API calls when
            generating fixes in parallel.
        max_retries: Maximum number of retries for transient AI API
            failures (rate limits, network errors). 0 disables retries.
        api_timeout: Timeout in seconds for each AI API call.
        validate_after_group: Whether to run validation immediately
            after each accepted group in interactive review.
        show_cost_estimate: Whether to display estimated cost before
            and after AI operations.
        context_lines: Number of lines of code context to include
            before and after the issue line when generating fixes.
        fix_search_radius: Number of lines to search above and below
            the target line when applying a fix.
        retry_base_delay: Initial delay in seconds before the first
            retry attempt.
        retry_max_delay: Maximum delay in seconds between retries.
        retry_backoff_factor: Multiplier applied to delay after each
            retry attempt.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled: bool = False
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str | None = None
    api_key_env: str | None = None
    default_fix: bool = False
    auto_apply: bool = False
    auto_apply_safe_fixes: bool = True
    max_tokens: int = Field(default=4096, ge=1)
    max_fix_issues: int = Field(default=20, ge=1)
    max_parallel_calls: int = Field(default=5, ge=1, le=20)
    max_retries: int = Field(default=2, ge=0, le=10)
    api_timeout: float = Field(default=60.0, ge=1.0)
    validate_after_group: bool = False
    show_cost_estimate: bool = True
    context_lines: int = Field(default=15, ge=1, le=100)
    fix_search_radius: int = Field(default=5, ge=1, le=50)
    retry_base_delay: float = Field(default=1.0, ge=0.1)
    retry_max_delay: float = Field(default=30.0, ge=1.0)
    retry_backoff_factor: float = Field(default=2.0, ge=1.0)

    @model_validator(mode="after")
    def _check_retry_delays(self) -> AIConfig:
        if self.retry_max_delay < self.retry_base_delay:
            msg = (
                f"retry_max_delay ({self.retry_max_delay}) must be >= "
                f"retry_base_delay ({self.retry_base_delay})"
            )
            raise ValueError(msg)
        return self
