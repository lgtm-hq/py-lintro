"""Env-var and CLI-flag overlays for AI configuration (#1970).

Exactly four environment variables map onto four ``ai:`` fields. Invalid
values fail at resolution with a calm diagnostic naming the variable (or
flag) and the accepted values — they never fall through to the config
default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_enum import AIProvider
from lintro.ai.resolved_ai_config import ResolvedAIConfig

__all__ = [
    "ENV_ENABLED",
    "ENV_MODEL",
    "ENV_PROVIDER",
    "ENV_TRANSPORT",
    "OVERRIDE_FIELDS",
    "apply_cli_overrides",
    "apply_env_overrides",
    "read_env_overrides",
]

ENV_PROVIDER = "LINTRO_AI_PROVIDER"
ENV_MODEL = "LINTRO_AI_MODEL"
ENV_TRANSPORT = "LINTRO_AI_TRANSPORT"
ENV_ENABLED = "LINTRO_AI_ENABLED"

OVERRIDE_FIELDS: tuple[str, ...] = ("provider", "model", "transport", "enabled")

_ENV_BY_FIELD: dict[str, str] = {
    "provider": ENV_PROVIDER,
    "model": ENV_MODEL,
    "transport": ENV_TRANSPORT,
    "enabled": ENV_ENABLED,
}

_ENABLED_TRUE = frozenset({"1", "true"})
_ENABLED_FALSE = frozenset({"0", "false"})
_ENABLED_ACCEPTED = "1, 0, true, false"

_FLAG_BY_FIELD: dict[str, str] = {
    "provider": "--provider",
    "model": "--model",
    "transport": "--transport",
}


def read_env_overrides() -> dict[str, Any]:
    """Read the four ``LINTRO_AI_*`` overrides that are present.

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
        overlay["enabled"] = _parse_enabled(enabled_raw)
    return overlay


def apply_env_overrides(
    config: AIConfig,
    sources: dict[str, ConfigSource],
) -> tuple[AIConfig, dict[str, ConfigSource]]:
    """Overlay environment values onto a parsed config.

    Args:
        config: Config built from the project ``ai:`` mapping.
        sources: Mutable provenance map for the override fields.

    Returns:
        The overlaid config and updated sources.
    """
    overlay = read_env_overrides()
    if not overlay:
        return config, sources
    updated = _apply_overlay(
        config=config,
        overlay=overlay,
        names=_ENV_BY_FIELD,
    )
    for field in overlay:
        sources[field] = ConfigSource.ENV
    return updated, sources


def apply_cli_overrides(
    resolved: ResolvedAIConfig,
    *,
    provider: str | None = None,
    model: str | None = None,
    transport: str | None = None,
) -> ResolvedAIConfig:
    """Apply ``lintro review`` CLI flags on top of a resolved config.

    Flags beat env vars. Omitted flags leave the corresponding field
    untouched. There is no ``--enabled`` flag.

    Args:
        resolved: Config + provenance after the env layer.
        provider: ``--provider`` value, or None when unset.
        model: ``--model`` value, or None when unset.
        transport: ``--transport`` value, or None when unset.

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
    if not overlay:
        return resolved
    updated = _apply_overlay(
        config=resolved.config,
        overlay=overlay,
        names=_FLAG_BY_FIELD,
    )
    sources = dict(resolved.sources)
    for field in overlay:
        sources[field] = ConfigSource.FLAG
    return ResolvedAIConfig(config=updated, sources=sources)


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


def _parse_enabled(raw: str) -> bool:
    """Parse ``LINTRO_AI_ENABLED`` into a bool.

    Args:
        raw: Stripped env value.

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
        f"{ENV_ENABLED}={raw!r} is not one of: {_ENABLED_ACCEPTED}",
    )


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
        **overlay,
        "lint": config.lint,
        "review": config.review,
    }
    try:
        payload = config.model_dump()
        payload.update(update)
        return AIConfig.model_validate(payload)
    except ValidationError as exc:
        raise AIConfigOverrideError(
            _describe_validation_error(exc=exc, overlay=overlay, names=names),
        ) from exc


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
        return ", ".join(provider.value for provider in AIProvider)
    if field == "transport":
        return ", ".join(transport.value for transport in AITransport)
    if field == "enabled":
        return _ENABLED_ACCEPTED
    return ""
