"""Facade over per-provider plugin metadata.

Every fact about a provider — its display name, default model, pricing,
API-key variable, CLI binary, contract and transports — is declared once, in
that provider's own package (``lintro/ai/providers/<name>/metadata.py``). This
module is the read side: it loads the in-tree plugins and hands their
:class:`~lintro.ai.providers.protocol.ProviderMetadata` records to the
consumers that used to keep parallel tables of the same facts (#2308).

Before that migration this module *held* those tables, as a frozen
``PROVIDERS`` registry that ``availability``, ``cost``, ``display.status`` and
the CLI contracts each partly duplicated. Nothing declares provider data here
any more; adding a vendor means adding an
:class:`~lintro.ai.provider_enum.AIProvider` member and a package, never
editing this file.

The lookups are deliberately functions rather than a module-level mapping: a
cached snapshot is the parallel table this replaced, and it would go stale the
moment a test swapped the registered plugins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_enum import AIProvider

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lintro.ai.provider_config import ProviderConfig
    from lintro.ai.providers.protocol import ProviderMetadata

__all__ = [
    "AIProvider",
    "DEFAULT_PRICING",
    "ModelPricing",
    "all_metadata",
    "config_model_for",
    "default_api_key_envs",
    "default_models",
    "metadata_for",
    "model_pricing",
    "provider_config_models",
    "retired_model_error",
    "retired_models_in_chain",
]

#: Fallback pricing for a model no provider publishes a price for.
DEFAULT_PRICING = ModelPricing(input_per_million=3.00, output_per_million=15.00)


def all_metadata() -> dict[AIProvider, ProviderMetadata]:
    """Return the metadata of every registered provider plugin.

    Returns:
        Metadata keyed by provider, in :class:`AIProvider` declaration order so
        callers never depend on plugin import order.
    """
    from lintro.ai.providers.builtins import load_builtin_providers
    from lintro.ai.providers.registry import all_providers

    load_builtin_providers()
    return {provider: plugin.metadata for provider, plugin in all_providers().items()}


def metadata_for(provider: AIProvider | str) -> ProviderMetadata:
    """Return one provider's metadata.

    Args:
        provider: Provider enum member, or the string a user typed.

    Returns:
        The provider's metadata record.
        :class:`~lintro.ai.exceptions.AIProviderNotRegisteredError` propagates
        from the registry lookup when *provider* is unknown, or is known but
        has no registered plugin.
    """
    from lintro.ai.providers.builtins import load_builtin_providers
    from lintro.ai.providers.registry import get_registered

    load_builtin_providers()
    return get_registered(provider).metadata


def model_pricing() -> dict[str, ModelPricing]:
    """Return every known model mapped to its pricing.

    Providers are merged in :class:`AIProvider` declaration order. Model
    identifiers are vendor-unique in practice, so the merge is a union rather
    than a precedence decision.

    Returns:
        A flat mapping of model identifier to :class:`ModelPricing`.
    """
    pricing: dict[str, ModelPricing] = {}
    for metadata in all_metadata().values():
        pricing.update(metadata.pricing)
    return pricing


def retired_model_error(
    model: str,
    *,
    provider: AIProvider | str | None,
) -> str | None:
    """Return why *model* cannot be sent to *provider*, when it retired it.

    Every surface that accepts a model id (config validation and the env and
    flag overlays that revalidate through it, custom review agents, the
    provider factory, the fallback chain, ``lintro doctor`` and the
    review-efficacy matrix check) asks this one question, so a retired id is
    refused with the same replacement everywhere, before any request is made
    (#2277).

    Only the selected provider's own retirement table is consulted: the same
    string sent to another provider, or to an OpenAI-compatible endpoint, is
    that provider's business and is not refused here.

    Args:
        model: The model identifier to check.
        provider: The provider the model would be sent to, as an enum member
            or the string a user typed. ``None``, or a name no plugin is
            registered under, has no retirement table and refuses nothing.

    Returns:
        A one-line refusal naming the retirement date and the replacement,
        or ``None`` when *provider* has not retired *model*.
    """
    from lintro.ai.exceptions import AIProviderNotRegisteredError
    from lintro.ai.model_lifecycle import retired_model_message

    if provider is None:
        return None
    try:
        metadata = metadata_for(provider)
    except (AIProviderNotRegisteredError, ValueError):
        return None
    retired = metadata.retired_models.get(model)
    if retired is None:
        return None
    return retired_model_message(
        model=model,
        retired=retired,
        vendor=metadata.display_name,
    )


def retired_models_in_chain(
    models: Iterable[str | None],
    *,
    provider: AIProvider | str | None,
) -> str | None:
    """Return the refusal for the first retired model in *models*.

    Args:
        models: Model identifiers in the order they would be tried; ``None``
            entries (the provider's own default) are skipped.
        provider: The provider every model in the chain would be sent to.

    Returns:
        The first refusal, or ``None`` when no model in the chain is retired.
    """
    for model in models:
        if model is None:
            continue
        error = retired_model_error(model, provider=provider)
        if error is not None:
            return error
    return None


def default_models() -> dict[AIProvider, str]:
    """Return each provider's default model identifier.

    Returns:
        Default model keyed by provider, in enum declaration order.
    """
    return {
        provider: metadata.default_model
        for provider, metadata in all_metadata().items()
    }


def default_api_key_envs() -> dict[AIProvider, str]:
    """Return each provider's default API-key environment variable.

    Returns:
        Variable name keyed by provider, in enum declaration order.
    """
    return {
        provider: metadata.default_api_key_env
        for provider, metadata in all_metadata().items()
    }


def provider_config_models() -> dict[AIProvider, type[ProviderConfig]]:
    """Return each provider's ``ai.providers.<name>`` block model.

    The models are declared in the provider packages, so this facade is the
    only thing the config layer needs to know about them (#2309): no central
    table maps a vendor-only key to the vendor that owns it.

    Returns:
        Block model keyed by provider, in :class:`AIProvider` declaration
        order.
    """
    from lintro.ai.providers.builtins import load_builtin_providers
    from lintro.ai.providers.registry import all_providers

    load_builtin_providers()
    return {
        provider: plugin.config_model for provider, plugin in all_providers().items()
    }


def config_model_for(provider: AIProvider | str) -> type[ProviderConfig]:
    """Return one provider's ``ai.providers.<name>`` block model.

    Args:
        provider: Provider enum member, or the string a user typed.

    Returns:
        The provider's :class:`~lintro.ai.provider_config.ProviderConfig`
        subclass. :class:`~lintro.ai.exceptions.AIProviderNotRegisteredError`
        propagates from the registry lookup when *provider* is unknown, or is
        known but has no registered plugin.
    """
    from lintro.ai.providers.builtins import load_builtin_providers
    from lintro.ai.providers.registry import get_registered

    load_builtin_providers()
    return get_registered(provider).config_model
