"""AI configuration model for Lintro.

Defines the AIConfig Pydantic model used in the ``ai:`` section of
.lintro-config.yaml. All AI features are opt-in and disabled by default.

Fields are logically grouped into three areas:

* **Provider** — model selection, API endpoints, authentication, retry
* **Budget** — cost caps, issue limits, parallelism, caching
* **Output** — display, verbosity, PR integration, apply behaviour

The flat attribute API (``config.provider``, ``config.max_tokens``, …)
is the primary interface; the grouping is for documentation only.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lintro.ai.config_views import AIBudgetConfig, AIOutputConfig, AIProviderConfig
from lintro.ai.enums import ConfidenceLevel, SanitizeMode
from lintro.ai.registry import AIProvider

# Re-export for backward compatibility
__all__ = [
    "AIBudgetConfig",
    "AIConfig",
    "AIOutputConfig",
    "AIProviderConfig",
]


class AIConfig(BaseModel):
    """Configuration for AI-powered features.

    All fields are accessible directly on the model instance
    (e.g. ``config.provider``).  For structured access, use the
    ``provider_config``, ``budget_config``, and ``output_config``
    properties which return frozen dataclass snapshots.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled: bool = False
    provider: AIProvider = AIProvider.ANTHROPIC
    model: str | None = None
    api_key_env: str | None = None
    api_base_url: str | None = Field(
        default=None,
        description=(
            "Custom API base URL. Enables Ollama, vLLM, Azure OpenAI, "
            "or any OpenAI-compatible endpoint."
        ),
    )
    api_region: str | None = Field(
        default=None,
        description=(
            "Provider region hint for data residency. "
            "Used with api_base_url for region-specific endpoints."
        ),
    )
    fallback_models: list[str] = Field(default_factory=list)
    default_fix: bool = False
    auto_apply: bool = False
    auto_apply_safe_fixes: bool = True
    max_tokens: int = Field(default=4096, ge=1, le=128_000)
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
    enable_cache: bool = Field(default=False)
    cache_ttl: int = Field(default=3600, ge=60)
    cache_max_entries: int = Field(default=1000, ge=1)
    max_refinement_attempts: int = Field(default=1, ge=0, le=3)
    fail_on_ai_error: bool = Field(default=False)
    fail_on_unfixed: bool = Field(
        default=False,
        description=(
            "When True, unfixable or failed AI fixes contribute to a "
            "non-zero exit code."
        ),
    )
    verbose: bool = Field(default=False)
    include_paths: list[str] = Field(
        default_factory=list,
        description="Glob patterns for paths to include in AI processing.",
    )
    exclude_paths: list[str] = Field(
        default_factory=list,
        description="Glob patterns for paths to exclude from AI processing.",
    )
    include_rules: list[str] = Field(
        default_factory=list,
        description="Glob patterns for rules to include in AI processing.",
    )
    exclude_rules: list[str] = Field(
        default_factory=list,
        description="Glob patterns for rules to exclude from AI processing.",
    )
    min_confidence: ConfidenceLevel = Field(
        default=ConfidenceLevel.LOW,
        description=(
            "Minimum confidence level for AI fix suggestions. "
            "Suggestions below this threshold are discarded. "
            "One of 'low', 'medium', 'high'."
        ),
    )
    github_pr_comments: bool = Field(
        default=False,
        description=(
            "Post AI summaries and fix suggestions as inline PR review "
            "comments when running in GitHub Actions."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Display AI fix suggestions without applying them. "
            "Useful for previewing what changes the AI would make."
        ),
    )
    max_cost_usd: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Maximum total cost in USD per AI session. " "None disables the limit."
        ),
    )
    max_prompt_tokens: int = Field(
        default=12000,
        ge=1000,
        description="Token budget for fix prompts before context trimming.",
    )
    stream: bool = Field(
        default=False,
        description="Stream AI responses token-by-token in interactive mode.",
    )
    sanitize_mode: SanitizeMode = Field(
        default=SanitizeMode.WARN,
        description=(
            "How to handle detected prompt injection patterns in source "
            "files: 'warn' logs and continues, 'block' skips the file, "
            "'off' disables detection."
        ),
    )

    @model_validator(mode="after")
    def _check_retry_delays(self) -> AIConfig:
        if self.retry_max_delay < self.retry_base_delay:
            msg = (
                f"retry_max_delay ({self.retry_max_delay}) must be >= "
                f"retry_base_delay ({self.retry_base_delay})"
            )
            raise ValueError(msg)
        return self

    # -- Grouped views -----------------------------------------------------

    @property
    def provider_config(self) -> AIProviderConfig:
        """Return a frozen snapshot of provider-related settings."""
        return AIProviderConfig(
            provider=self.provider,
            model=self.model,
            api_key_env=self.api_key_env,
            api_base_url=self.api_base_url,
            api_region=self.api_region,
            fallback_models=tuple(self.fallback_models),
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            api_timeout=self.api_timeout,
            retry_base_delay=self.retry_base_delay,
            retry_max_delay=self.retry_max_delay,
            retry_backoff_factor=self.retry_backoff_factor,
        )

    @property
    def budget_config(self) -> AIBudgetConfig:
        """Return a frozen snapshot of budget and limit settings."""
        return AIBudgetConfig(
            max_fix_issues=self.max_fix_issues,
            max_parallel_calls=self.max_parallel_calls,
            max_cost_usd=self.max_cost_usd,
            max_prompt_tokens=self.max_prompt_tokens,
            max_refinement_attempts=self.max_refinement_attempts,
            enable_cache=self.enable_cache,
            cache_ttl=self.cache_ttl,
            cache_max_entries=self.cache_max_entries,
            context_lines=self.context_lines,
            fix_search_radius=self.fix_search_radius,
        )

    @property
    def output_config(self) -> AIOutputConfig:
        """Return a frozen snapshot of output and display settings."""
        return AIOutputConfig(
            show_cost_estimate=self.show_cost_estimate,
            verbose=self.verbose,
            stream=self.stream,
            dry_run=self.dry_run,
            github_pr_comments=self.github_pr_comments,
            validate_after_group=self.validate_after_group,
            auto_apply=self.auto_apply,
            auto_apply_safe_fixes=self.auto_apply_safe_fixes,
            default_fix=self.default_fix,
            fail_on_ai_error=self.fail_on_ai_error,
            fail_on_unfixed=self.fail_on_unfixed,
            min_confidence=self.min_confidence,
            sanitize_mode=self.sanitize_mode,
            include_paths=tuple(self.include_paths),
            exclude_paths=tuple(self.exclude_paths),
            include_rules=tuple(self.include_rules),
            exclude_rules=tuple(self.exclude_rules),
        )
