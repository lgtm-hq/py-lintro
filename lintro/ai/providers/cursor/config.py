"""Cursor-specific AI settings (#2309).

Declared next to :data:`~lintro.ai.providers.cursor.metadata.CURSOR_METADATA`
so the knob, its default and its legacy spelling live in the Cursor package
rather than on the shared :class:`~lintro.ai.config.AIConfig`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lintro.ai.config import AIConfig

__all__ = ["CursorConfig", "cursor_settings"]


class CursorConfig(ProviderConfig):
    """Settings only the Cursor provider reads."""

    legacy_keys: ClassVar[Mapping[str, str]] = MappingProxyType(
        {"cursor_trust_workspace": "trust_workspace"},
    )

    trust_workspace: bool = Field(
        default=True,
        description=(
            "Pass '--trust' to the Cursor 'agent' CLI, granting it workspace "
            "trust. Trust follows from choosing provider: cursor, so this "
            "defaults to True. Set false to restore the Cursor agent's "
            "interactive trust prompt."
        ),
    )


def cursor_settings(config: AIConfig) -> CursorConfig:
    """Return the effective Cursor block from *config*.

    Args:
        config: Effective AI configuration for this run.

    Returns:
        The resolved ``ai.providers.cursor`` block, or a default-valued one
        when the config declares none.
    """
    block = config.provider_settings(AIProvider.CURSOR)
    return block if isinstance(block, CursorConfig) else CursorConfig()
