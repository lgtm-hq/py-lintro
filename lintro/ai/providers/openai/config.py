"""OpenAI-specific AI settings (#2309).

OpenAI reads no vendor-only knob today: model, base URL, region and token
budget are shared fields every provider honours. The empty block is still
declared so every plugin answers ``config_model`` and ``ai.providers.openai``
is a recognized (if currently featureless) section rather than a typo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = ["OpenAIConfig", "openai_settings"]


class OpenAIConfig(ProviderConfig):
    """Settings only the OpenAI provider reads.

    Empty on purpose — see the module docstring.
    """


def openai_settings(config: AIConfig) -> OpenAIConfig:
    """Return the effective OpenAI block from *config*.

    Args:
        config: Effective AI configuration for this run.

    Returns:
        The resolved ``ai.providers.openai`` block, or a default-valued one
        when the config declares none.
    """
    block = config.provider_settings(AIProvider.OPENAI)
    return block if isinstance(block, OpenAIConfig) else OpenAIConfig()
