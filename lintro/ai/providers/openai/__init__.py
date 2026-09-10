"""OpenAI provider package.

Importing this package registers
:class:`~lintro.ai.providers.openai.plugin.OpenAIPlugin` and nothing else: the
``openai`` SDK is pulled only when
:meth:`~lintro.ai.providers.openai.plugin.OpenAIPlugin.build` imports
:mod:`lintro.ai.providers.openai.provider`. Import that module directly to
reach :class:`~lintro.ai.providers.openai.provider.OpenAIProvider`.
"""

from __future__ import annotations

from lintro.ai.providers.openai.config import OpenAIConfig
from lintro.ai.providers.openai.metadata import OPENAI_METADATA
from lintro.ai.providers.openai.plugin import OpenAIPlugin
from lintro.ai.providers.registry import register_provider

__all__ = ["OPENAI_METADATA", "PLUGIN", "OpenAIConfig", "OpenAIPlugin"]

#: The registered plugin instance. ``register_provider`` returns what it was
#: given, so this both registers and names the singleton discovery re-reads
#: after a test clears the registry.
PLUGIN = register_provider(OpenAIPlugin())
