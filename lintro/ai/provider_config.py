"""Base model for provider-specific AI settings (#2309).

A knob that only one vendor understands does not belong on
:class:`~lintro.ai.config.AIConfig`. It lives on a small pydantic model
declared next to that provider's plugin metadata
(``lintro/ai/providers/<name>/config.py``) and reaches users as
``ai.providers.<name>.<field>``.

The plugin protocol exposes the model as
:attr:`~lintro.ai.providers.protocol.ProviderPlugin.config_model`, so the
resolver can build the right block for a provider without a central table of
which vendor owns which key — the same single-source-of-truth rule #2308
applied to :class:`~lintro.ai.providers.protocol.ProviderMetadata`.

Legacy top-level spellings are declared here too, on
:attr:`ProviderConfig.legacy_keys`, so the provider that owns a knob also owns
its migration. The shim is temporary; see the removal issue linked from
``docs/ai-features.md``.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

from loguru import logger
from pydantic import BaseModel, ConfigDict

__all__ = [
    "LEGACY_KEY_REMOVAL_ISSUE",
    "ProviderConfig",
    "legacy_key_field_paths",
    "legacy_key_warning",
    "migrate_legacy_provider_keys",
    "reset_legacy_key_warnings",
]


class ProviderConfig(BaseModel):
    """One provider's own settings, nested under ``ai.providers.<name>``.

    Subclasses declare the fields that vendor understands and nothing else.
    An empty subclass is meaningful: it says the provider takes no
    vendor-specific knobs today, and it keeps every plugin answering
    ``config_model`` so callers never branch on "does this one have a block".

    Blocks are frozen because they are resolved once per invocation and then
    read by the plugin, doctor and the display surfaces; a mutable snapshot
    would be a second override path.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Legacy top-level ``ai.<key>`` spellings mapped to this model's field
    #: names, accepted for one release. Declared by the provider that owns the
    #: knob so no central migration table exists.
    legacy_keys: ClassVar[Mapping[str, str]] = MappingProxyType({})


#: Legacy top-level keys already warned about in this process. The shim must
#: say its piece once per run, not once per resolution: display surfaces
#: re-resolve the same mapping the execution path already reported on.
_LEGACY_KEYS_WARNED: set[str] = set()

#: Issue tracking removal of the legacy top-level shim, named in the warning so
#: a user can follow the deprecation rather than guess at its timeline.
LEGACY_KEY_REMOVAL_ISSUE = 2464


def reset_legacy_key_warnings() -> None:
    """Forget which legacy keys have been warned about.

    The once-per-run guard is process-global, which is what "once per run"
    means for a CLI. Tests that assert on the warning need to re-arm it.
    """
    _LEGACY_KEYS_WARNED.clear()


def legacy_key_warning(*, legacy_key: str, provider: str, field: str) -> str:
    """Build the deprecation message for one legacy top-level key.

    Args:
        legacy_key: The top-level ``ai.<key>`` spelling that was used.
        provider: Value of the provider that owns the knob.
        field: Field name inside that provider's block.

    Returns:
        A one-line message naming the new path and the removal issue.
    """
    return (
        f"ai.{legacy_key} is deprecated and will be removed in a future "
        f"release (#{LEGACY_KEY_REMOVAL_ISSUE}); move it to "
        f"ai.providers.{provider}.{field}."
    )


def migrate_legacy_provider_keys(
    data: dict[str, Any],
    *,
    diagnostics: bool = True,
) -> dict[str, Any]:
    """Fold legacy top-level provider keys into ``ai.providers.<name>``.

    Each provider's block model declares its own legacy spellings on
    :attr:`ProviderConfig.legacy_keys`, so this reads the plugin registry
    rather than a central migration table. A nested value already present wins
    over the legacy key — the shim is a fallback, never an override — but the
    deprecation is still reported, because the legacy key is still in the file.

    Args:
        data: Raw ``ai:`` mapping. Not mutated.
        diagnostics: Whether this parse may emit the deprecation. Display-only
            callers pass False, like every other migration hint on this path:
            a summary re-parses the mapping the execution path already
            reported on and must not duplicate its output. Suppressed keys are
            not marked as warned, so the execution path still says its piece.

    Returns:
        A copy with legacy keys removed and their values moved under
        ``providers``. Returns *data* unchanged when it carries no legacy key.

    Raises:
        ValueError: If ``providers`` is present but is not a mapping. The
            migration would otherwise replace it with an empty mapping and the
            malformed value would never reach validation.
    """
    from lintro.ai.registry import provider_config_models

    models = provider_config_models()
    present = {
        legacy: (provider, field)
        for provider, model in models.items()
        for legacy, field in model.legacy_keys.items()
        if legacy in data
    }
    if not present:
        return data

    migrated = dict(data)
    providers_raw = migrated.get("providers")
    if providers_raw is not None and not isinstance(providers_raw, Mapping):
        # Overwriting a malformed scalar with ``{}`` would let the migrated
        # legacy key stand in for it, so a bad ``ai.providers`` value would be
        # accepted instead of reported.
        raise ValueError(
            "ai.providers must be a mapping of provider blocks, got "
            f"{type(providers_raw).__name__}",
        )
    providers: dict[Any, Any] = dict(providers_raw) if providers_raw else {}
    for legacy, (provider, field) in present.items():
        value = migrated.pop(legacy)
        key = provider.value
        block_raw = providers.get(key, providers.get(provider))
        block = dict(block_raw) if isinstance(block_raw, Mapping) else {}
        if isinstance(block_raw, ProviderConfig):
            block = block_raw.model_dump()
        block.setdefault(field, value)
        providers.pop(provider, None)
        providers[key] = block
        if not diagnostics or legacy in _LEGACY_KEYS_WARNED:
            continue
        _LEGACY_KEYS_WARNED.add(legacy)
        message = legacy_key_warning(
            legacy_key=legacy,
            provider=key,
            field=field,
        )
        warnings.warn(message, DeprecationWarning, stacklevel=2)
        logger.warning(message)
    migrated["providers"] = providers
    return migrated


def legacy_key_field_paths() -> dict[str, str]:
    """Map each legacy top-level key to the nested path it now lives at.

    Returns:
        ``{"cursor_trust_workspace": "providers.cursor.trust_workspace", ...}``
        for every legacy spelling any registered provider still accepts.
    """
    from lintro.ai.registry import provider_config_models

    return {
        legacy: f"providers.{provider.value}.{field}"
        for provider, model in provider_config_models().items()
        for legacy, field in model.legacy_keys.items()
    }
