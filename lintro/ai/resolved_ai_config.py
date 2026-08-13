"""Effective AI configuration plus per-field provenance (#1970)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.enums.config_source import ConfigSource

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = [
    "ResolvedAIConfig",
    "format_sourced_value",
]


def format_sourced_value(value: str, source: ConfigSource | str | None) -> str:
    """Append a provenance annotation when a source is known.

    Args:
        value: Display value (provider name, model id, transport).
        source: Field provenance, or empty/None to leave *value* unchanged.

    Returns:
        ``value`` or ``value (source)``.
    """
    if source is None or source == "":
        return value
    label = source.value if isinstance(source, ConfigSource) else str(source)
    return f"{value} ({label})"


@dataclass(frozen=True, slots=True)
class ResolvedAIConfig:
    """Validated effective AI settings together with field provenance.

    Attributes:
        config: Effective :class:`~lintro.ai.config.AIConfig` after
            env/flag overlays and Pydantic validation.
        sources: Provenance for the override fields (``provider``,
            ``model``, ``transport``, ``enabled``).
    """

    config: AIConfig
    sources: Mapping[str, ConfigSource]

    def source_of(self, field: str) -> ConfigSource:
        """Return provenance for *field*, defaulting to ``default``.

        Args:
            field: Override-field name.

        Returns:
            The recorded source, or :attr:`ConfigSource.DEFAULT` when absent.
        """
        return self.sources.get(field, ConfigSource.DEFAULT)
