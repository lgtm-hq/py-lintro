"""Env-var and CLI-flag overlays for AI configuration (#1970, #2024, #2153).

Exactly six environment variables map onto six shared ``ai:`` fields. Invalid
values fail at resolution with a calm diagnostic naming the variable (or
flag) and the accepted values — they never fall through to the config
default.

Provider-specific fields live under ``ai.providers.<name>`` (#2309) and are
overlaid on the same layers by the same functions here, so there is one
override path and not two: ``LINTRO_AI_PROVIDERS__<PROVIDER>__<FIELD>`` on the
env layer and ``--provider-option <field>=<value>`` on the flag layer, both
carrying provenance under the ``providers.<name>.<field>`` key.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_blocks import nested_source_key
from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider, accepted_provider_values
from lintro.ai.resolved_ai_config import ResolvedAIConfig

__all__ = [
    "ENV_ENABLED",
    "ENV_PROVIDER_BLOCK_PREFIX",
    "ENV_MAX_COST_USD",
    "ENV_MODEL",
    "ENV_PROVIDER",
    "ENV_REVIEW",
    "ENV_TRANSPORT",
    "OVERRIDE_FIELDS",
    "apply_cli_overrides",
    "apply_env_overrides",
    "read_env_overrides",
    "read_provider_block_env_overrides",
]

ENV_PROVIDER = "LINTRO_AI_PROVIDER"
ENV_MODEL = "LINTRO_AI_MODEL"
ENV_TRANSPORT = "LINTRO_AI_TRANSPORT"
ENV_ENABLED = "LINTRO_AI_ENABLED"
ENV_REVIEW = "LINTRO_AI_REVIEW"
ENV_MAX_COST_USD = "LINTRO_AI_MAX_COST_USD"

#: Prefix of the per-provider block overrides: the remainder is the provider
#: name and the field name, upper-cased and joined by a double underscore
#: (``LINTRO_AI_PROVIDERS__CURSOR__TRUST_WORKSPACE``). Double underscore
#: because provider and field names may each contain a single one.
ENV_PROVIDER_BLOCK_PREFIX = "LINTRO_AI_PROVIDERS__"

#: Separator between the provider and field segments of a block override.
_ENV_BLOCK_SEPARATOR = "__"

#: Flag spelling of a per-provider block override, for error text.
_PROVIDER_OPTION_FLAG = "--provider-option"

OVERRIDE_FIELDS: tuple[str, ...] = (
    "provider",
    "model",
    "transport",
    "enabled",
    "review",
    "max_cost_usd",
)

_ENV_BY_FIELD: dict[str, str] = {
    "provider": ENV_PROVIDER,
    "model": ENV_MODEL,
    "transport": ENV_TRANSPORT,
    "enabled": ENV_ENABLED,
    "review": ENV_REVIEW,
    "max_cost_usd": ENV_MAX_COST_USD,
}

_ENABLED_TRUE = frozenset({"1", "true"})
_ENABLED_FALSE = frozenset({"0", "false"})
_ENABLED_ACCEPTED = "1, 0, true, false"
_MAX_COST_ACCEPTED = "a positive number (USD cap), or uncapped"
_UNCAP_SENTINEL = "uncapped"
_ZERO_OVERLAY_ERROR = "ambiguous — use 'uncapped' or a positive value"

_FLAG_BY_FIELD: dict[str, str] = {
    "provider": "--provider",
    "model": "--model",
    "transport": "--transport",
    "review": "--review/--no-review",
    "max_cost_usd": "--max-cost-usd",
}


def read_env_overrides() -> dict[str, Any]:
    """Read the six ``LINTRO_AI_*`` overrides that are present.

    Unset or whitespace-only variables are omitted (layer absent). There is
    no meta-gate variable.

    Returns:
        Field-name to raw/parsed value for every set override.
    """
    overlay: dict[str, Any] = {}
    provider = _env_text(ENV_PROVIDER)
    if provider is not None:
        overlay["provider"] = provider
    model = _env_text(ENV_MODEL)
    if model is not None:
        overlay["model"] = model
    transport = _env_text(ENV_TRANSPORT)
    if transport is not None:
        overlay["transport"] = transport
    enabled_raw = _env_text(ENV_ENABLED)
    if enabled_raw is not None:
        overlay["enabled"] = _parse_bool_override(
            enabled_raw,
            name=ENV_ENABLED,
        )
    review_raw = _env_text(ENV_REVIEW)
    if review_raw is not None:
        overlay["review"] = _parse_bool_override(
            review_raw,
            name=ENV_REVIEW,
        )
    max_cost_raw = _env_text(ENV_MAX_COST_USD)
    if max_cost_raw is not None:
        overlay["max_cost_usd"] = _parse_max_cost_usd(
            max_cost_raw,
            name=ENV_MAX_COST_USD,
        )
    return overlay


def read_provider_block_env_overrides() -> dict[AIProvider, dict[str, Any]]:
    """Read every ``LINTRO_AI_PROVIDERS__<PROVIDER>__<FIELD>`` override.

    Values are parsed by the field's own model, so a bad spelling fails here
    with the variable named rather than reaching the config as a string.

    Returns:
        Parsed field values keyed by provider, empty when no such variable is
        set.

    Raises:
        AIConfigOverrideError: If a variable names an unknown provider, an
            unknown field of a known provider, or carries a value that
            provider's block model rejects.
    """
    from lintro.ai.exceptions import AIProviderNotRegisteredError
    from lintro.ai.registry import config_model_for

    overrides: dict[AIProvider, dict[str, Any]] = {}
    for name in sorted(os.environ):
        if not name.startswith(ENV_PROVIDER_BLOCK_PREFIX):
            continue
        raw = _env_text(name)
        if raw is None:
            continue
        remainder = name[len(ENV_PROVIDER_BLOCK_PREFIX) :]
        provider_token, separator, field_token = remainder.partition(
            _ENV_BLOCK_SEPARATOR,
        )
        if not separator or not field_token:
            raise AIConfigOverrideError(
                f"{name} is not a provider block override; the shape is "
                f"{ENV_PROVIDER_BLOCK_PREFIX}<PROVIDER>__<FIELD>",
            )
        try:
            model = config_model_for(provider_token.lower())
        except (AIProviderNotRegisteredError, ValueError) as exc:
            raise AIConfigOverrideError(
                f"{name} names provider {provider_token.lower()!r}, which is "
                f"not one of: {accepted_provider_values()}",
            ) from exc
        field = field_token.lower()
        if field not in model.model_fields:
            raise AIConfigOverrideError(
                f"{name}: {provider_token.lower()} has no setting {field!r}; "
                f"accepted: {_accepted_block_fields(model)}",
            )
        provider = AIProvider(provider_token.lower())
        overrides.setdefault(provider, {})[field] = _parse_block_value(
            model=model,
            field=field,
            raw=raw,
            name=name,
        )
    return overrides


def _accepted_block_fields(model: type[ProviderConfig]) -> str:
    """List a provider block's settable fields for an error message.

    Args:
        model: The provider's block model.

    Returns:
        Comma-separated field names, or a note that the block takes none.
    """
    fields = ", ".join(sorted(model.model_fields))
    return fields or "(this provider takes no block settings)"


def _parse_block_value(
    *,
    model: type[ProviderConfig],
    field: str,
    raw: object,
    name: str,
) -> Any:
    """Coerce one raw override value through the block model's own validation.

    Args:
        model: The provider's block model.
        field: Field being overridden.
        raw: Raw env or flag value.
        name: Variable or flag spelling, for the error message.

    Returns:
        The validated value.

    Raises:
        AIConfigOverrideError: If the block model rejects *raw*.
    """
    try:
        return getattr(model.model_validate({field: raw}), field)
    except ValidationError as exc:
        accepted = _accepted_block_value_hint(model=model, field=field)
        suffix = f" is not one of: {accepted}" if accepted else " is not valid"
        raise AIConfigOverrideError(f"{name}={raw!r}{suffix}") from exc


def _accepted_block_value_hint(
    *,
    model: type[ProviderConfig],
    field: str,
) -> str:
    """Return the accepted spellings for one block field, when enumerable.

    Args:
        model: The provider's block model.
        field: Field name.

    Returns:
        Comma-separated accepted values, or empty for free-form fields.
    """
    annotation = model.model_fields[field].annotation
    if annotation is bool:
        return _ENABLED_ACCEPTED
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return ", ".join(str(member.value) for member in annotation)
    return ""


def _merge_provider_blocks(
    *,
    config: AIConfig,
    blocks: Mapping[AIProvider, Mapping[str, Any]],
) -> dict[AIProvider, ProviderConfig]:
    """Layer field overrides onto the config's existing provider blocks.

    An overlay sets one field; the rest of that provider's block keeps the
    value the project config (or the model default) gave it.

    Args:
        config: Config the overlay sits on top of.
        blocks: Field values to apply, keyed by provider.

    Returns:
        The full ``providers`` mapping to hand back to validation.
    """
    merged = dict(config.providers)
    for provider, fields in blocks.items():
        merged[provider] = config.provider_settings(provider).model_copy(
            update=dict(fields),
        )
    return merged


def apply_env_overrides(
    config: AIConfig,
    sources: dict[str, ConfigSource],
) -> tuple[AIConfig, dict[str, ConfigSource]]:
    """Overlay environment values onto a parsed config.

    Both layers are read here: the six flat ``LINTRO_AI_*`` variables and the
    ``LINTRO_AI_PROVIDERS__<PROVIDER>__<FIELD>`` block overrides (#2309).

    Args:
        config: Config built from the project ``ai:`` mapping.
        sources: Mutable provenance map for the override fields.

    Returns:
        The overlaid config and updated sources.
    """
    overlay = read_env_overrides()
    blocks = read_provider_block_env_overrides()
    if not overlay and not blocks:
        return config, sources
    flat_fields = tuple(overlay)
    if blocks:
        overlay["providers"] = _merge_provider_blocks(
            config=config,
            blocks=blocks,
        )
    updated = _apply_overlay(
        config=config,
        overlay=overlay,
        names=_ENV_BY_FIELD,
    )
    for field in flat_fields:
        sources[field] = ConfigSource.ENV
    for provider, fields in blocks.items():
        for field in fields:
            sources[nested_source_key(provider=provider, field=field)] = (
                ConfigSource.ENV
            )
    return updated, sources


def apply_cli_overrides(
    resolved: ResolvedAIConfig,
    *,
    provider: str | None = None,
    model: str | None = None,
    transport: str | None = None,
    review: bool | None = None,
    max_cost_usd: float | str | None = None,
    provider_options: Mapping[str, str] | None = None,
) -> ResolvedAIConfig:
    """Apply ``lintro review`` CLI flags on top of a resolved config.

    Flags beat env vars. Omitted flags leave the corresponding field
    untouched, and a blank or whitespace-only value is treated as unset
    for every string-valued flag, ``--max-cost-usd`` included. There is no
    ``--enabled`` flag. ``uncapped`` (any case) lifts the ceiling. Overlay
    ``0`` is rejected as ambiguous (#2154).
    Overlaying ``max_cost_usd`` also stamps both transport-profile cost
    fields so ``apply_resolved_transport`` cannot clobber flag/env with a
    YAML profile cap (#2024).

    Args:
        resolved: Config + provenance after the env layer.
        provider: ``--provider`` value, or None when unset.
        model: ``--model`` value, or None when unset.
        transport: ``--transport`` value, or None when unset.
        review: ``--review/--no-review`` value, or None when unset.
        max_cost_usd: ``--max-cost-usd`` value, or None/blank when unset.
        provider_options: ``--provider-option name=value`` pairs for the
            effective provider's ``ai.providers.<name>`` block, or None when
            none were passed.

    ``AIConfigOverrideError`` propagates from
    :func:`_resolve_provider_option_flags` when *provider_options* is
    non-empty while no provider is selected, names a field the provider's
    block model does not declare, or carries a value it rejects.

    Returns:
        A new resolved config when any flag is set; *resolved* otherwise.
    """
    overlay: dict[str, Any] = {}
    if provider is not None and str(provider).strip():
        overlay["provider"] = str(provider).strip()
    if model is not None and str(model).strip():
        overlay["model"] = str(model).strip()
    if transport is not None and str(transport).strip():
        overlay["transport"] = str(transport).strip()
    if review is not None:
        overlay["review"] = review
    if max_cost_usd is not None and str(max_cost_usd).strip():
        overlay["max_cost_usd"] = _parse_max_cost_usd(
            max_cost_usd,
            name=_FLAG_BY_FIELD["max_cost_usd"],
        )
    blocks = _resolve_provider_option_flags(
        config=resolved.config,
        provider_overlay=overlay.get("provider"),
        provider_options=provider_options,
    )
    if not overlay and not blocks:
        return resolved
    flat_fields = tuple(overlay)
    if blocks:
        overlay["providers"] = _merge_provider_blocks(
            config=resolved.config,
            blocks=blocks,
        )
    updated = _apply_overlay(
        config=resolved.config,
        overlay=overlay,
        names=_FLAG_BY_FIELD,
    )
    sources = dict(resolved.sources)
    for field in flat_fields:
        sources[field] = ConfigSource.FLAG
    for provider, fields in blocks.items():
        for field in fields:
            sources[nested_source_key(provider=provider, field=field)] = (
                ConfigSource.FLAG
            )
    return ResolvedAIConfig(config=updated, sources=sources)


def _resolve_provider_option_flags(
    *,
    config: AIConfig,
    provider_overlay: object,
    provider_options: Mapping[str, str] | None,
) -> dict[AIProvider, dict[str, Any]]:
    """Bind ``--provider-option`` pairs to the effective provider's block.

    The flags apply to whichever provider this invocation actually uses, so a
    ``--provider`` flag in the same overlay is honoured before the config
    value: ``--provider cursor --provider-option trust_workspace=false`` means
    what it reads as.

    Args:
        config: Config after the project and env layers.
        provider_overlay: ``--provider`` value in this same overlay, if any.
        provider_options: ``name=value`` pairs, or None when none were passed.

    Returns:
        Parsed field values keyed by the effective provider, empty when no
        option flags were passed.

    Raises:
        AIConfigOverrideError: If no provider is selected, the provider is
            unknown, a field is not declared by that provider's block model,
            or a value is rejected by it.
    """
    if not provider_options:
        return {}
    from lintro.ai.exceptions import AIProviderNotRegisteredError
    from lintro.ai.registry import config_model_for

    raw_provider = provider_overlay if provider_overlay is not None else config.provider
    if raw_provider is None:
        raise AIConfigOverrideError(
            f"{_PROVIDER_OPTION_FLAG} needs a provider; set ai.provider, "
            f"{ENV_PROVIDER}, or --provider first",
        )
    try:
        provider = AIProvider(str(raw_provider))
        model = config_model_for(provider)
    except (AIProviderNotRegisteredError, ValueError) as exc:
        raise AIConfigOverrideError(
            f"{_PROVIDER_OPTION_FLAG} names provider {str(raw_provider)!r}, "
            f"which is not one of: {accepted_provider_values()}",
        ) from exc

    parsed: dict[str, Any] = {}
    for field, raw in provider_options.items():
        key = field.strip().lower().replace("-", "_")
        if key not in model.model_fields:
            raise AIConfigOverrideError(
                f"{_PROVIDER_OPTION_FLAG} {field}=...: {provider.value} has no "
                f"setting {key!r}; accepted: {_accepted_block_fields(model)}",
            )
        parsed[key] = _parse_block_value(
            model=model,
            field=key,
            raw=raw,
            name=f"{_PROVIDER_OPTION_FLAG} {key}",
        )
    return {provider: parsed}


def _env_text(name: str) -> str | None:
    """Return a stripped env value, or None when the layer is absent.

    Args:
        name: Environment variable name.

    Returns:
        The stripped value, or None when unset or whitespace-only.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _parse_bool_override(raw: str, *, name: str) -> bool:
    """Parse a boolean ``LINTRO_AI_*`` override.

    Args:
        raw: Stripped env value.
        name: Environment variable name for error text.

    Returns:
        True for ``1``/``true``; False for ``0``/``false`` (case-insensitive).

    Raises:
        AIConfigOverrideError: If *raw* is not an accepted spelling.
    """
    key = raw.lower()
    if key in _ENABLED_TRUE:
        return True
    if key in _ENABLED_FALSE:
        return False
    raise AIConfigOverrideError(
        f"{name}={raw!r} is not one of: {_ENABLED_ACCEPTED}",
    )


def _parse_max_cost_usd(raw: object, *, name: str) -> float | None:
    """Parse a cost-cap override into a USD ceiling, or None if uncapped.

    ``uncapped`` (case-insensitive) lifts the ceiling; only ``None``
    disables :class:`~lintro.ai.budget.CostBudget`. ``0`` is *not* a
    synonym for uncapped anywhere: :class:`CostBudget` treats ``0.0`` as a
    hard $0 cap that raises on the first budgeted call, and YAML
    ``ai.max_cost_usd: 0`` keeps exactly that meaning (per ADR 0006).
    Overlay ``0`` is rejected rather than silently reinterpreted — it was
    the #2024 spelling for uncapped and is now ambiguous against the
    literal $0 YAML cap (#2154). Never copy a ``0`` between the two
    surfaces: write ``uncapped`` for an overlay that lifts the ceiling.

    Args:
        raw: Env-var string or CLI float.
        name: Variable or flag name for the error message.

    Returns:
        A positive finite float, or None when the cap is lifted.

    Raises:
        AIConfigOverrideError: If *raw* is ``0``, negative, non-numeric,
            or non-finite.
    """
    text = str(raw).strip()
    if text.lower() == _UNCAP_SENTINEL:
        return None
    try:
        value = float(text)
    except ValueError:
        raise AIConfigOverrideError(
            f"{name}={raw!r} is not one of: {_MAX_COST_ACCEPTED}",
        ) from None
    if not math.isfinite(value) or value < 0:
        raise AIConfigOverrideError(
            f"{name}={raw!r} is not one of: {_MAX_COST_ACCEPTED}",
        )
    if value == 0:
        raise AIConfigOverrideError(
            f"{name}={raw!r} is {_ZERO_OVERLAY_ERROR}",
        )
    return value


def _apply_overlay(
    *,
    config: AIConfig,
    overlay: Mapping[str, Any],
    names: Mapping[str, str],
) -> AIConfig:
    """Copy *config* with *overlay* applied through Pydantic validation.

    ``lint`` and ``review`` are passed through so the legacy
    ``ai.enabled``-only default cannot fire when the master switch comes
    from an overlay: ``LINTRO_AI_ENABLED=1`` must not imply ``ai.review``.

    Args:
        config: Base configuration.
        overlay: Field updates to apply.
        names: Field name to env-var or flag name, for error text.

    Returns:
        A validated copy of *config*.

    Raises:
        AIConfigOverrideError: If Pydantic rejects an overlay value.
    """
    update: dict[str, Any] = {
        "lint": config.lint,
        "review": config.review,
        **overlay,
    }
    try:
        payload = config.model_dump()
        payload.update(update)
        if "max_cost_usd" in overlay:
            _stamp_overlay_cost_on_profiles(
                payload,
                overlay["max_cost_usd"],
            )
        return AIConfig.model_validate(payload)
    except ValidationError as exc:
        raise AIConfigOverrideError(
            _describe_validation_error(exc=exc, overlay=overlay, names=names),
        ) from exc


def _stamp_overlay_cost_on_profiles(
    payload: dict[str, Any],
    max_cost_usd: float | None,
) -> None:
    """Write an overlay cost cap onto both transport profiles.

    ``resolve_transport_settings`` prefers profile caps over the legacy
    scalar. Flag/env overlays must beat those YAML profile fields (#2024),
    matching how ``--timeout`` stamps the active profile.

    Args:
        payload: ``model_dump()`` of the config being overlaid.
        max_cost_usd: Overlay ceiling, or None when uncapped.
    """
    transports = dict(payload.get("transports") or {})
    api = dict(transports.get("api") or {})
    cli = dict(transports.get("cli") or {})
    api["max_cost_usd"] = max_cost_usd
    cli["max_cost_usd_advisory"] = max_cost_usd
    payload["transports"] = {**transports, "api": api, "cli": cli}


def _describe_validation_error(
    *,
    exc: ValidationError,
    overlay: Mapping[str, Any],
    names: Mapping[str, str],
) -> str:
    """Build a calm diagnostic naming the override and accepted values.

    Args:
        exc: Pydantic validation error from the overlay copy.
        overlay: Field updates that were attempted.
        names: Field name to env-var or flag name.

    Returns:
        A one-line message such as
        ``LINTRO_AI_PROVIDER='cursur' is not one of: anthropic, openai, cursor``.
    """
    for error in exc.errors():
        loc = error.get("loc", ())
        if not loc:
            continue
        field = str(loc[0])
        if field not in overlay:
            continue
        raw = overlay[field]
        name = names.get(field, field)
        accepted = _accepted_values(field)
        if accepted:
            return f"{name}={raw!r} is not one of: {accepted}"
        return f"{name}={raw!r} is not a valid value for ai.{field}"
    return str(exc)


def _accepted_values(field: str) -> str:
    """Return the comma-separated accepted values for an enum field.

    Args:
        field: Override-field name.

    Returns:
        Accepted values, or empty when the field is free-form.
    """
    if field == "provider":
        return accepted_provider_values()
    if field == "transport":
        return ", ".join(transport.value for transport in AITransport)
    if field in {"enabled", "review"}:
        return _ENABLED_ACCEPTED
    if field == "max_cost_usd":
        return _MAX_COST_ACCEPTED
    return ""
