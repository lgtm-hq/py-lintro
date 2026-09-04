"""Effective AI configuration plus per-field provenance (#1970)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.enums.config_source import ConfigSource

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = [
    "MAX_COST_LABEL",
    "ResolvedAIConfig",
    "format_max_cost_label",
    "format_sourced_value",
]


#: One display label for the cost cap across status, terminal, and PR
#: surfaces, so the same field is never spelled three ways (#2048).
MAX_COST_LABEL = "Max cost"

#: Caps below this render with extra decimals so a sub-cent ceiling is not
#: displayed as ``$0.00`` (#2048).
_SUB_CENT = 0.01


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


def format_max_cost_label(
    max_cost_usd: float | None,
    source: ConfigSource | str | None = None,
) -> str:
    """Format a cost cap for display, including provenance.

    ``None`` renders as ``uncapped`` so a lifted ceiling is never silent
    (#2024). Sub-cent caps get four decimals so ``--max-cost-usd 0.004``
    does not read as a $0 ceiling (#2048).

    Args:
        max_cost_usd: Effective USD cap, or None when unlimited.
        source: Field provenance, or empty/None to omit the suffix.

    Returns:
        ``$1.50 (env)``, ``$0.0040 (flag)``, ``uncapped (flag)``, or the
        bare label when *source* is absent.
    """
    if max_cost_usd is None:
        value = "uncapped"
    elif 0 < max_cost_usd < _SUB_CENT:
        value = f"${max_cost_usd:.4f}"
    else:
        value = f"${max_cost_usd:.2f}"
    return format_sourced_value(value, source)


@dataclass(frozen=True, slots=True)
class ResolvedAIConfig:
    """Validated effective AI settings together with field provenance.

    Attributes:
        config: Effective :class:`~lintro.ai.config.AIConfig` after
            env/flag overlays and Pydantic validation.
        sources: Provenance for the override fields (``provider``,
            ``model``, ``transport``, ``enabled``, ``review``,
            ``max_cost_usd``).
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
