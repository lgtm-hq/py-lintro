"""Astro-check tool package.

Everything the ``astro-check`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.astro_check.definition`. ``lintro.tools.definitions.astro_check``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.astro_check.definition import (
    ASTRO_CHECK_DEFAULT_PRIORITY,
    ASTRO_CHECK_DEFAULT_TIMEOUT,
    ASTRO_CHECK_FILE_PATTERNS,
    ASTRO_CHECK_OPTION_TYPES,
    AstroCheckPlugin,
)

__all__ = [
    "ASTRO_CHECK_DEFAULT_PRIORITY",
    "ASTRO_CHECK_DEFAULT_TIMEOUT",
    "ASTRO_CHECK_FILE_PATTERNS",
    "ASTRO_CHECK_OPTION_TYPES",
    "AstroCheckPlugin",
]
