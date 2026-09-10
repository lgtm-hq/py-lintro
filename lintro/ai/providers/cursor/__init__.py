"""Cursor provider package.

Importing this package registers
:class:`~lintro.ai.providers.cursor.plugin.CursorPlugin` and nothing else.
Import :mod:`lintro.ai.providers.cursor.provider` directly to
reach :class:`~lintro.ai.providers.cursor.provider.CursorProvider`.
"""

from __future__ import annotations

from lintro.ai.providers.cursor.config import CursorConfig
from lintro.ai.providers.cursor.metadata import CURSOR_METADATA
from lintro.ai.providers.cursor.plugin import CursorPlugin
from lintro.ai.providers.registry import register_provider

__all__ = ["CURSOR_METADATA", "PLUGIN", "CursorConfig", "CursorPlugin"]

#: The registered plugin instance. ``register_provider`` returns what it was
#: given, so this both registers and names the singleton discovery re-reads
#: after a test clears the registry.
PLUGIN = register_provider(CursorPlugin())
