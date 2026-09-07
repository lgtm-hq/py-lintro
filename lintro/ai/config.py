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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lintro.ai.config_views import AIBudgetConfig, AIOutputConfig, AIProviderConfig
from lintro.ai.enums import (
    AITransport,
    CliBareMode,
    ConfidenceLevel,
    SanitizeMode,
)
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.provider_enum import accepted_provider_values
from lintro.ai.registry import AIProvider
from lintro.ai.resolved_ai_config import ResolvedAIConfig

__all__ = [
    "AIBudgetConfig",
    "AIConfig",
    "AIOutputConfig",
    "AIProviderConfig",
    "AITransportProfiles",
    "ApiTransportProfile",
    "CliTransportProfile",
    "ResolvedAIConfig",
]


class ApiTransportProfile(BaseModel):
    """Operational knobs for the metered API transport (#1923)."""

    model_config = ConfigDict(extra="forbid")

    timeout: float | None = Field(
        default=None,
        ge=1.0,
        description="Stream-sized per-call timeout in seconds (default 60).",
    )
    max_cost_usd: float | None = Field(
        default=None,
        ge=0,
        description="Enforced spend ceiling for metered API billing.",
    )


class CliTransportProfile(BaseModel):
    """Operational knobs for the subscription CLI transport (#1923)."""

    model_config = ConfigDict(extra="forbid")

    timeout: float | None = Field(
        default=None,
        ge=1.0,
        description="Per-chunk CLI invocation timeout in seconds (default 1800).",
    )
    max_cost_usd_advisory: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Advisory cost bound under subscription billing; lintro cannot "
            "enforce spend on the CLI path."
        ),
    )


class AITransportProfiles(BaseModel):
    """Transport-scoped AI review profiles (#1923)."""

    model_config = ConfigDict(extra="forbid")

    api: ApiTransportProfile = Field(default_factory=ApiTransportProfile)
    cli: CliTransportProfile = Field(default_factory=CliTransportProfile)


_SUPPRESS_DIAGNOSTICS: ContextVar[bool] = ContextVar(
    "ai_config_suppress_diagnostics",
    default=False,
)


@contextmanager
def _suppressed_diagnostics() -> Iterator[None]:
    """Silence validator-emitted diagnostics for the duration of the block.

    Model validators on :class:`AIConfig` log migration hints as a side
    effect of construction. Display-only parses re-build a config that the
    resolver already parsed, so they must not repeat those hints.

    Yields:
        None: Control, with diagnostics suppressed on the current context.
    """
    token = _SUPPRESS_DIAGNOSTICS.set(True)
    try:
        yield
    finally:
        _SUPPRESS_DIAGNOSTICS.reset(token)


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
    provider: AIProvider | None = Field(
        default=None,
        description=(
            "Required when any AI feature (ai.lint or ai.review) is enabled. "
            "Set via `ai.provider` in config, LINTRO_AI_PROVIDER, or --provider. "
            f"Accepted providers: {accepted_provider_values()}."
        ),
    )
    transport: AITransport | None = Field(
        default=None,
        description=(
            "Required when any AI feature (ai.lint or ai.review) is enabled. "
            "How to invoke the provider: 'api' (SDK) or 'cli' (local binary)."
        ),
    )
    transports: AITransportProfiles = Field(
        default_factory=AITransportProfiles,
        description=(
            "Per-transport operational profiles (timeout and cost caps). "
            "Resolution: transport profile → legacy api_timeout/max_cost_usd "
            "→ built-in default (api: 60s; cli: 1800s)."
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
    max_parallel_calls: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Concurrent AI provider calls for fixes and review chunk fan-out. "
            "Honored even when max_cost_usd is set. With n concurrent calls and "
            "no per-call reserve estimate, a session may overshoot max_cost_usd "
            "by up to n − 1 in-flight calls' cost."
        ),
    )
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
            "Maximum total cost in USD per AI session. None disables the limit. "
            "A cost cap does not serialize provider calls: under "
            "max_parallel_calls=n concurrent workers the final total may "
            "exceed this ceiling by up to n − 1 in-flight calls' cost."
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
    cli_bare: CliBareMode = Field(
        default=CliBareMode.AUTO,
        description=(
            "Whether the anthropic CLI transport passes '--bare' to the "
            "'claude' binary. '--bare' drops the CLI's agentic tool surface "
            "but also disables OAuth session login, so it only authenticates "
            "against an API key. 'auto' (default) sends it only when an API "
            "key is reachable (ANTHROPIC_API_KEY or a configured "
            "apiKeyHelper), so subscription logins keep working; 'always' and "
            "'never' force the choice. Overridable per run with the "
            "LINTRO_CLI_BARE environment variable."
        ),
    )

    cursor_trust_workspace: bool = Field(
        default=True,
        description=(
            "Pass '--trust' to the Cursor 'agent' CLI, granting it workspace "
            "trust. Trust follows from choosing provider: cursor, so this "
            "defaults to True. Set false to restore the Cursor agent's "
            "interactive trust prompt."
        ),
    )

    checkpoint_retention: int = Field(
        default=10,
        ge=0,
        description=(
            "Total git checkpoint refs to retain under "
            "refs/lintro/checkpoints/, this run's included; older refs are "
            "pruned first. Default 10; 0 keeps only the current run's ref."
        ),
    )
    checkpoint_fmt: bool = Field(
        default=False,
        description=(
            "When True, capture a git checkpoint before `lintro fmt` mutates "
            "files, so `git diff <ref>` and "
            "`git restore --source=<ref> --worktree -- <path>` can review or "
            "undo the run. Git-only: nothing is captured outside a git work "
            "tree."
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
    cli_max_diff_tokens: int = Field(
        default=24_000,
        ge=1_000,
        description=(
            "Per-chunk diff token budget under --transport cli. The "
            "context-window budget alone is far too large for a single CLI "
            "call (timeout / 32k output-token exhaustion on ~1.5k-line PRs); "
            "this ceiling forces the semantic chunker to split large diffs."
        ),
    )
    cli_max_diff_bytes: int = Field(
        default=1_500_000,
        ge=10_000,
        description=(
            "Hard ceiling on the full unified-diff byte size under "
            "--transport cli. Diffs above this fail with an actionable "
            "advisory to use --paths filtering or --transport api instead of "
            "spawning an unbounded number of CLI chunks."
        ),
    )
    cli_max_findings_per_call: int = Field(
        default=12,
        ge=1,
        le=50,
        description=(
            "Maximum findings a single CLI review call may emit. Bounds the "
            "JSON response so the model cannot hit the ~32k output-token cap "
            "mid-object; overflow is summarized rather than truncated."
        ),
    )
    transcript_logging: bool = Field(
        default=False,
        description=(
            "Write raw AI provider request/response traffic as NDJSON under "
            ".lintro-cache/ai/transcripts/. Off by default. Can also be "
            "enabled with LINTRO_AI_TRANSCRIPT=1. Payloads are secret-redacted "
            "before write; auth headers and API keys are never logged."
        ),
    )
    transcript_retention: int = Field(
        default=10,
        ge=1,
        description=(
            "Maximum number of AI transcript NDJSON files to retain under "
            ".lintro-cache/ai/transcripts/. Older files are pruned when a "
            "new transcript writer starts."
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
            if _SUPPRESS_DIAGNOSTICS.get():
                return self
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

    # -- Construction from raw config data ---------------------------------

    @classmethod
    def resolve_from_mapping(
        cls,
        data: Mapping[str, Any] | None,
        *,
        diagnostics: bool = True,
    ) -> ResolvedAIConfig:
        """Parse ``ai:`` into effective values plus per-field provenance.

        Only recognized keys are passed through, so the model's own defaults
        apply to every omitted field. Unknown keys are dropped rather than
        rejected, because ``AIConfig`` itself forbids extras and a stale key
        in ``.lintro-config.yaml`` must not break the whole run. This is also
        the boundary that keeps :mod:`lintro.config` free of any knowledge of
        ``AIConfig``'s field set (issue #724): the loader stores the ``ai:``
        section verbatim and the AI layer parses it.

        Env overlays are applied after the mapping is validated so a
        ``LINTRO_AI_ENABLED=1`` overlay cannot trigger the legacy
        ``ai.enabled``-only sub-toggle default. Invalid env values fail
        here and never fall through to the config default.

        This is the project + environment half of resolution only; the CLI
        overlay sits on top of it. :func:`lintro.ai.effective_config.
        resolve_effective_ai_config` is the single production caller that
        composes both (#2299) — surfaces call that function, never this
        method directly.

        Args:
            data: Raw ``ai`` section from config, or None when absent.
            diagnostics: Whether this parse may emit user-facing diagnostics
                — the dropped-unknown-key warning and the validators'
                migration hints. Display-only callers pass False, because
                they render values the execution path already reported on and
                must not duplicate its output.

        Returns:
            Validated config together with provenance for ``provider``,
            ``model``, ``transport``, ``enabled``, ``review``, and
            ``max_cost_usd``.
        """
        from lintro.ai.config_overrides import (
            OVERRIDE_FIELDS,
            apply_env_overrides,
        )

        filtered: dict[str, Any] = {}
        if data:
            known_fields = set(cls.model_fields)
            unknown = set(data) - known_fields
            if unknown and diagnostics:
                logger.warning(
                    "Unknown AI config keys ignored: {}",
                    ", ".join(sorted(unknown)),
                )
            filtered = {k: v for k, v in data.items() if k in known_fields}

        if diagnostics:
            config = cls(**filtered) if filtered else cls()
        else:
            with _suppressed_diagnostics():
                config = cls(**filtered) if filtered else cls()

        sources: dict[str, ConfigSource] = {
            field: (ConfigSource.CONFIG if field in filtered else ConfigSource.DEFAULT)
            for field in OVERRIDE_FIELDS
        }
        config, sources = apply_env_overrides(config, sources)
        return ResolvedAIConfig(config=config, sources=sources)

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
            cli_bare=self.cli_bare,
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
            cli_max_diff_tokens=self.cli_max_diff_tokens,
            cli_max_diff_bytes=self.cli_max_diff_bytes,
            cli_max_findings_per_call=self.cli_max_findings_per_call,
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
