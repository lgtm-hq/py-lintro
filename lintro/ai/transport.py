"""Transport resolution helpers for AI products."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = ["apply_transport_override"]


def apply_transport_override(
    ai_config: AIConfig,
    transport: str | AITransport | None,
) -> AIConfig:
    """Apply a CLI transport override on top of config.

    Args:
        ai_config: Loaded AI configuration.
        transport: Optional ``api`` or ``cli`` override from the CLI flag.

    Returns:
        Config unchanged when *transport* is ``None``, otherwise a copy
        with ``transport`` replaced.
    """
    if transport is None:
        return ai_config
    transport_enum = (
        transport
        if isinstance(transport, AITransport)
        else AITransport(str(transport).lower())
    )
    return ai_config.model_copy(update={"transport": transport_enum})
