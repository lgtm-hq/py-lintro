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

import warnings

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lintro.ai.config_views import AIBudgetConfig, AIOutputConfig, AIProviderConfig
from lintro.ai.enums import AITransport, ConfidenceLevel, SanitizeMode
from lintro.ai.registry import AIProvider

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

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for all AI features. ANDs with the per-feature "
            "toggles ai.lint and ai.review. When true with neither sub-toggle "
            "set explicitly, both are enabled for backward compatibility "
            "(deprecated: set ai.lint and/or ai.review explicitly)."
        ),
    )
    lint: bool = Field(
        default=False,
        description=(
            "Enable AI lint summarization after check/fix runs. Effective only "
            "when ai.enabled is also true (the two are ANDed)."
        ),
    )
    review: bool = Field(
        default=False,
        description=(
            "Enable the `lintro review` AI diff-review command. Effective only "
            "when ai.enabled is also true (the two are ANDed)."
        ),
    )
    provider: AIProvider = AIProvider.ANTHROPIC
    transport: AITransport | None = Field(
        default=None,
        description=(
            "Required when any AI feature (ai.lint or ai.review) is enabled. "
            "How to invoke the provider: 'api' (SDK) or 'cli' (local binary)."
        ),
    )
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
    max_fix_attempts: int = Field(
        default=20,
        ge=1,
        description="Maximum number of issues to attempt fixing per run. "
        "Counts API calls made, not suggestions returned.",
    )
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
            "Maximum total cost in USD per AI session." " None disables the limit."
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
    cursor_trust_workspace: bool = Field(
        default=False,
        description=(
            "Pass '--trust' to the Cursor 'agent' CLI, granting it workspace "
            "trust. Security risk: the Cursor provider is fed untrusted, "
            "prompt-injectable content (e.g. 'lintro review --pr N' embeds "
            "diffs from arbitrary fork PRs). Combining workspace trust with "
            "such input could let an injected diff drive an agent operating "
            "with full workspace trust, so this defaults to False and should "
            "only be enabled for fully trusted local workspaces."
        ),
    )

    review_allow_unredacted_git_native: bool = Field(
        default=False,
        description=(
            "Allow the git-native (CLI transport) review path to delegate "
            "diff retrieval to the provider by emitting a 'git diff' command "
            "instead of embedding the diff. Security risk: a delegated diff "
            "is produced by the provider itself and never passes through "
            "lintro's secret-redaction choke point, so secrets present in "
            "the diff can reach the provider's backend unredacted. Defaults "
            "to False so redaction always wins: lintro embeds the redacted "
            "diff in the prompt even for large diffs. Only enable this for "
            "trusted diffs with no secrets concern when the efficiency of "
            "delegated git retrieval on very large diffs is required."
        ),
    )

    @model_validator(mode="after")
    def _apply_legacy_enabled_default(self) -> AIConfig:
        """Enable both sub-toggles for legacy ``ai.enabled``-only configs.

        Prior to the ai.lint / ai.review split, ``ai.enabled: true`` turned on
        both AI lint summarization and AI review. To preserve that behaviour,
        when ``enabled`` is true but neither sub-toggle was set explicitly, both
        are switched on and a deprecation warning is emitted.

        Returns:
            The validated configuration instance.
        """
        fields_set = self.model_fields_set
        if self.enabled and "lint" not in fields_set and "review" not in fields_set:
            self.lint = True
            self.review = True
            message = (
                "ai.enabled without ai.lint/ai.review is deprecated; both AI "
                "lint summarization and AI review were enabled for backward "
                "compatibility. Set ai.lint and/or ai.review explicitly."
            )
            # DeprecationWarning from library code is ignored by Python's default
            # filters; also log so installed-CLI users see the migration hint.
            warnings.warn(message, DeprecationWarning, stacklevel=2)
            logger.warning(message)
        return self

    @model_validator(mode="after")
    def _validate_transport_and_retries(self) -> AIConfig:
        if self.retry_max_delay < self.retry_base_delay:
            msg = (
                f"retry_max_delay ({self.retry_max_delay}) must be >= "
                f"retry_base_delay ({self.retry_base_delay})"
            )
            raise ValueError(msg)
        return self

    # -- Effective feature state -------------------------------------------

    @property
    def lint_enabled(self) -> bool:
        """Whether AI lint summarization is active.

        Returns:
            True when both the master switch and the lint sub-toggle are on.
        """
        return self.enabled and self.lint

    @property
    def review_enabled(self) -> bool:
        """Whether the AI review command is active.

        Returns:
            True when both the master switch and the review sub-toggle are on.
        """
        return self.enabled and self.review

    @property
    def any_feature_enabled(self) -> bool:
        """Whether any AI feature (lint summary or review) is active.

        Returns:
            True when either lint_enabled or review_enabled is true.
        """
        return self.lint_enabled or self.review_enabled

    # -- Grouped views -----------------------------------------------------

    @property
    def provider_config(self) -> AIProviderConfig:
        """Return a frozen snapshot of provider-related settings."""
        return AIProviderConfig(
            provider=self.provider,
            transport=self.transport,
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
            max_fix_attempts=self.max_fix_attempts,
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
