"""Read-only grouped views of AI configuration settings.

These frozen dataclasses provide structured access to logically related
subsets of :class:`~lintro.ai.config.AIConfig` fields. They are returned
by the ``provider_config``, ``budget_config``, and ``output_config``
properties on ``AIConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.enums import ConfidenceLevel, SanitizeMode
from lintro.ai.registry import AIProvider


@dataclass(frozen=True)
class AIProviderConfig:
    """Read-only view of provider-related AI settings."""

    provider: AIProvider
    model: str | None
    api_key_env: str | None
    api_base_url: str | None
    api_region: str | None
    fallback_models: list[str]
    max_tokens: int
    max_retries: int
    api_timeout: float
    retry_base_delay: float
    retry_max_delay: float
    retry_backoff_factor: float


@dataclass(frozen=True)
class AIBudgetConfig:
    """Read-only view of budget and limit settings."""

    max_fix_issues: int
    max_parallel_calls: int
    max_cost_usd: float | None
    max_prompt_tokens: int
    max_refinement_attempts: int
    enable_cache: bool
    cache_ttl: int
    cache_max_entries: int
    context_lines: int
    fix_search_radius: int


@dataclass(frozen=True)
class AIOutputConfig:
    """Read-only view of output and display settings."""

    show_cost_estimate: bool
    verbose: bool
    stream: bool
    dry_run: bool
    github_pr_comments: bool
    validate_after_group: bool
    auto_apply: bool
    auto_apply_safe_fixes: bool
    default_fix: bool
    fail_on_ai_error: bool
    fail_on_unfixed: bool
    min_confidence: ConfidenceLevel
    sanitize_mode: SanitizeMode
    include_paths: list[str]
    exclude_paths: list[str]
    include_rules: list[str]
    exclude_rules: list[str]
