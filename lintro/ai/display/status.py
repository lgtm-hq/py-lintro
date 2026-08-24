"""Pre-execution AI status rendering.

Renders the ``AI`` rows of the pre-execution configuration summary. This
lives in the AI package so the core summary renderer
(:mod:`lintro.utils.console.pre_execution_summary`) does not import
:mod:`lintro.ai`. See issue #724.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.resolved_ai_config import (
    ResolvedAIConfig,
    format_max_cost_label,
    format_sourced_value,
)

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

#: Line shown when no AI configuration is available at all.
AI_STATUS_NO_CONFIG = "[dim]disabled (no config)[/dim]"


def render_ai_status(
    *,
    ai_config: AIConfig | ResolvedAIConfig | Mapping[str, Any] | None,
    is_ci: bool,
) -> list[str]:
    """Render the pre-execution AI status lines.

    Args:
        ai_config: Raw ``ai:`` mapping as held by the core executor, an
            already-parsed :class:`AIConfig`, a :class:`ResolvedAIConfig`
            carrying provenance, or None when unavailable. A mapping is
            parsed here with diagnostics off, because rendering a summary
            must not emit unknown-key warnings or migration hints (the
            resolver on the AI entry path already reports them).
        is_ci: Whether the run is in a CI environment (affects the
            ``auto_apply`` warning wording).

    Returns:
        Rich-markup lines describing AI availability and settings. Never
        empty: a disabled or missing configuration still renders one line.
    """
    ai_parts: list[str] = []
    if ai_config is None:
        ai_parts.append(AI_STATUS_NO_CONFIG)
        return ai_parts

    sources: Mapping[str, ConfigSource] | None = None
    resolved_for_cost: ResolvedAIConfig | None = None
    if isinstance(ai_config, ResolvedAIConfig):
        resolved_for_cost = ai_config
        sources = ai_config.sources
        ai_config = ai_config.config
    elif isinstance(ai_config, Mapping):
        from lintro.ai.config import AIConfig as _AIConfig

        resolved = _AIConfig.resolve_from_mapping(ai_config, diagnostics=False)
        resolved_for_cost = resolved
        sources = resolved.sources
        ai_config = resolved.config

    if not ai_config.enabled:
        disabled = "[dim]disabled[/dim]"
        if sources is not None and sources.get("enabled") is ConfigSource.ENV:
            disabled = "[dim]disabled (env)[/dim]"
        ai_parts.append(disabled)
        return ai_parts

    import os

    from lintro.ai.availability import is_provider_available
    from lintro.ai.provider_enum import (
        accepted_provider_values,
        provider_required_error,
    )
    from lintro.ai.providers import get_default_model
    from lintro.ai.registry import PROVIDERS, AIProvider

    if ai_config.provider is None:
        provider_name = ""
        ai_parts.append("[yellow]enabled (provider unset)[/yellow]")
        if ai_config.any_feature_enabled:
            ai_parts.append(f"  [yellow]{provider_required_error()}[/yellow]")
    else:
        provider_name = str(ai_config.provider).lower()
    supported = set(AIProvider)

    # Check: unknown provider
    if provider_name and provider_name not in supported:
        ai_parts.append("[red]enabled (unknown provider)[/red]")
        names = accepted_provider_values()
        ai_parts.append(
            f"  [yellow]'{ai_config.provider}' is not supported. Use: {names}[/yellow]",
        )
    elif provider_name:
        # Check SDK availability
        sdk_ok = is_provider_available(provider_name)

        # Check API key
        key_env = ai_config.api_key_env or PROVIDERS.default_api_key_envs.get(
            AIProvider(provider_name),
            "",
        )
        key_set = bool(os.environ.get(key_env)) if key_env else False

        if sdk_ok and key_set:
            ai_parts.append("[green]enabled[/green]")
        elif not sdk_ok:
            ai_parts.append(
                "[red]enabled (SDK not installed)[/red]",
            )
            ai_parts.append(
                "  [yellow]run: uv pip install 'lintro\\[ai]'[/yellow]",
            )
        elif not key_set:
            ai_parts.append(
                "[yellow]enabled (API key missing)[/yellow]",
            )
            ai_parts.append(
                f"  [yellow]set {key_env} env var[/yellow]",
            )

    provider_label = (
        str(ai_config.provider) if ai_config.provider is not None else "unset"
    )
    if sources is not None:
        provider_label = format_sourced_value(
            provider_label,
            sources.get("provider"),
        )
    ai_parts.append(f"  provider: {provider_label}")

    effective_model = ai_config.model or (
        get_default_model(provider_name) if provider_name else None
    )
    if effective_model:
        model_label = effective_model
        if sources is not None:
            model_label = format_sourced_value(
                model_label,
                sources.get("model"),
            )
        elif not ai_config.model:
            model_label += " [dim](default)[/dim]"
        ai_parts.append(f"  model: {model_label}")

    if sources is not None:
        transport_value = (
            ai_config.transport.value if ai_config.transport is not None else "unset"
        )
        ai_parts.append(
            "  transport: "
            + format_sourced_value(transport_value, sources.get("transport")),
        )
        from lintro.ai.transport import resolve_max_cost_with_source

        cap, cap_source = (
            resolve_max_cost_with_source(resolved_for_cost)
            if resolved_for_cost is not None
            else (
                ai_config.max_cost_usd,
                sources.get("max_cost_usd"),
            )
        )
        ai_parts.append(
            "  max_cost_usd: "
            + format_max_cost_label(
                max_cost_usd=cap,
                source=cap_source,
            ),
        )

    # auto_apply warning
    if ai_config.auto_apply:
        if is_ci:
            ai_parts.append("  auto-apply: [green]on[/green]")
        else:
            ai_parts.append(
                "  auto-apply: [bold red]on (files will be "
                "modified without confirmation)[/bold red]",
            )

    # Parallel workers
    ai_parts.append(
        f"  parallel: {ai_config.max_parallel_calls} workers",
    )
    ai_parts.append(
        "  safe-auto-apply: "
        + (
            "[green]on[/green]" if ai_config.auto_apply_safe_fixes else "[dim]off[/dim]"
        ),
    )
    ai_parts.append(
        "  verify-fixes: "
        + ("[green]on[/green]" if ai_config.validate_after_group else "[dim]off[/dim]"),
    )
    return ai_parts
