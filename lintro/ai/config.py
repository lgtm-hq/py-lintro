"""AI configuration model for Lintro.

Defines the AIConfig Pydantic model used in the ``ai:`` section of
.lintro-config.yaml. All AI features are opt-in and disabled by default.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lintro.ai.enums import ConfidenceLevel, SanitizeMode
from lintro.ai.registry import AIProvider


class AIConfig(BaseModel):
    """Configuration for AI-powered features.

    Attributes:
        model_config: Pydantic model configuration for mutability and
            extra-field handling.
        enabled: Whether AI features are enabled globally.
        provider: AI provider to use ("anthropic" or "openai").
        model: Model identifier. None uses the provider's default.
        api_key_env: Custom environment variable name for the API key.
            Defaults to provider-specific env var (ANTHROPIC_API_KEY,
            OPENAI_API_KEY).
        api_base_url: Custom API base URL. Enables Ollama, vLLM,
            Azure OpenAI, or any OpenAI-compatible endpoint.
        api_region: Provider region hint for data residency. Used
            with api_base_url for region-specific endpoints.
        fallback_models: Ordered list of fallback model identifiers.
            On rate-limit errors, the orchestrator retries with each
            model in sequence before giving up.
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
        enable_cache: Whether to enable on-disk suggestion caching for
            deduplication across runs. Defaults to False.
        cache_ttl: Time-to-live in seconds for cached suggestions.
            Defaults to 3600 (1 hour). Minimum 60.
        cache_max_entries: Maximum number of cache entries to keep.
            When exceeded, least recently used entries are evicted.
            Defaults to 1000.
        max_refinement_attempts: Maximum number of refinement rounds
            for unverified fixes. 0 disables refinement.
        fail_on_ai_error: Whether to re-raise AI exceptions instead of
            logging and continuing gracefully.
        fail_on_unfixed: When True, unfixable or failed AI fixes
            contribute to a non-zero exit code.
        verbose: Whether to emit detailed progress and diagnostic
            messages for AI operations.
        include_paths: Glob patterns for paths to include in AI processing.
        exclude_paths: Glob patterns for paths to exclude from AI processing.
        include_rules: Glob patterns for rules to include in AI processing.
        exclude_rules: Glob patterns for rules to exclude from AI processing.
        min_confidence: Minimum confidence level for AI fix suggestions.
            One of 'low', 'medium', 'high'.
        github_pr_comments: Post AI summaries and fix suggestions as
            inline PR review comments when running in GitHub Actions.
        dry_run: Display AI fix suggestions without applying them.
        max_cost_usd: Maximum total cost in USD per AI session.
            None disables the limit.
        max_prompt_tokens: Token budget for fix prompts before context
            trimming.
        stream: Stream AI responses token-by-token in interactive mode.
        sanitize_mode: Controls prompt injection detection behavior.
            'warn' logs detections, 'block' skips affected files,
            'off' disables detection.
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
