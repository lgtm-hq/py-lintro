"""Resolution helpers for the ``ai.providers`` blocks (#2309).

Naming, error text and provenance seeding for the nested provider blocks.
They live beside :mod:`lintro.ai.provider_config` rather than in
:mod:`lintro.ai.config` so the flat model keeps declaring fields and nothing
else, and so a surface that only needs a provenance key never imports
``AIConfig``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider

if TYPE_CHECKING:
    from pydantic import ValidationError

__all__ = [
    "describe_block_error",
    "nested_source_key",
    "nested_sources",
    "provider_label",
]


def provider_label(name: Any) -> str:
    """Return the config-file spelling of a provider key.

    Args:
        name: Provider enum member or the string a user wrote.

    Returns:
        The value users type in ``ai.providers.<name>``.
    """
    return name.value if isinstance(name, AIProvider) else str(name)


def describe_block_error(*, name: Any, exc: ValidationError) -> str:
    """Describe a rejected provider block using the user's own key path.

    Args:
        name: Provider key the block was written under.
        exc: Validation error raised by that provider's block model.

    Returns:
        A one-line message naming ``ai.providers.<name>.<field>``.
    """
    label = provider_label(name)
    first = exc.errors()[0]
    loc = first.get("loc", ())
    field = ".".join(str(part) for part in loc)
    path = f"ai.providers.{label}.{field}" if field else f"ai.providers.{label}"
    return f"{path}: {first.get('msg', 'invalid value')}"


def nested_source_key(*, provider: AIProvider | str, field: str) -> str:
    """Return the provenance key for one nested provider field.

    Nested fields share the flat fields' provenance map rather than a second
    one, so a surface reads ``resolved.sources`` the same way whichever kind
    of field it is displaying (#2309).

    Args:
        provider: Provider that owns the block.
        field: Field name inside that block.

    Returns:
        ``providers.<provider>.<field>``.
    """
    return f"providers.{provider_label(provider)}.{field}"


def nested_sources(
    *,
    raw: Mapping[str, Any],
    legacy_paths: Mapping[str, str],
) -> dict[str, ConfigSource]:
    """Seed provenance for every registered provider's block fields.

    A field written in the project ``ai:`` mapping — under ``providers`` or
    through its legacy top-level spelling — is ``config``; everything else
    starts at ``default`` and is raised by the env and flag layers on top.

    Args:
        raw: The recognized subset of the project ``ai:`` mapping.
        legacy_paths: Legacy top-level key to ``providers.<name>.<field>``.

    Returns:
        Provenance keyed by :func:`nested_source_key`.
    """
    from lintro.ai.registry import provider_config_models

    written: set[str] = {path for key, path in legacy_paths.items() if key in raw}
    blocks = raw.get("providers")
    if isinstance(blocks, Mapping):
        for name, block in blocks.items():
            fields = (
                block.model_fields_set
                if isinstance(block, ProviderConfig)
                else (block if isinstance(block, Mapping) else {})
            )
            for field in fields:
                written.add(nested_source_key(provider=name, field=field))

    sources: dict[str, ConfigSource] = {}
    for provider, model in provider_config_models().items():
        for field in model.model_fields:
            key = nested_source_key(provider=provider, field=field)
            sources[key] = (
                ConfigSource.CONFIG if key in written else ConfigSource.DEFAULT
            )
    return sources
