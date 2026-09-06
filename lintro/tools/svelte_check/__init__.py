"""Svelte-check tool package.

Everything the ``svelte-check`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.svelte_check.definition`. ``lintro.tools.definitions.svelte_check``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.svelte_check.definition import (
    SVELTE_CHECK_DEFAULT_PRIORITY,
    SVELTE_CHECK_DEFAULT_TIMEOUT,
    SVELTE_CHECK_FILE_PATTERNS,
    SvelteCheckPlugin,
)

__all__ = [
    "SVELTE_CHECK_DEFAULT_PRIORITY",
    "SVELTE_CHECK_DEFAULT_TIMEOUT",
    "SVELTE_CHECK_FILE_PATTERNS",
    "SvelteCheckPlugin",
]
