"""Env-var and CLI-flag overlays for AI configuration (#1970, #2024, #2153).

Exactly six environment variables map onto six ``ai:`` fields. Invalid
values fail at resolution with a calm diagnostic naming the variable (or
flag) and the accepted values — they never fall through to the config
default.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_enum import accepted_provider_values
from lintro.ai.resolved_ai_config import ResolvedAIConfig

__all__ = [
    "ENV_ENABLED",
    "ENV_MAX_COST_USD",
    "ENV_MODEL",
    "ENV_PROVIDER",
    "ENV_REVIEW",
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
ENV_REVIEW = "LINTRO_AI_REVIEW"
ENV_MAX_COST_USD = "LINTRO_AI_MAX_COST_USD"

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
    review: bool | None = None,
    max_cost_usd: float | str | None = None,
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
