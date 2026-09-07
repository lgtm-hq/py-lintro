"""Anthropic provider package.

Importing this package registers
:class:`~lintro.ai.providers.anthropic.plugin.AnthropicPlugin` and nothing
else: the ``anthropic`` SDK is pulled only when
:meth:`~lintro.ai.providers.anthropic.plugin.AnthropicPlugin.build` imports
:mod:`lintro.ai.providers.anthropic.provider`. Import that module directly to
reach :class:`~lintro.ai.providers.anthropic.provider.AnthropicProvider`.
"""

from __future__ import annotations

from lintro.ai.providers.anthropic.metadata import ANTHROPIC_METADATA
from lintro.ai.providers.anthropic.plugin import AnthropicPlugin
from lintro.ai.providers.registry import register_provider

__all__ = ["ANTHROPIC_METADATA", "PLUGIN", "AnthropicPlugin"]

#: The registered plugin instance. ``register_provider`` returns what it was
#: given, so this both registers and names the singleton discovery re-reads
#: after a test clears the registry.
PLUGIN = register_provider(AnthropicPlugin())
