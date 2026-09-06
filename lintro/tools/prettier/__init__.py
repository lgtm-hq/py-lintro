"""Prettier tool package.

Everything the ``prettier`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.prettier.definition`. ``lintro.tools.definitions.prettier``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.prettier.definition import (
    PRETTIER_CONFIG_FILENAMES,
    PRETTIER_DEFAULT_PRIORITY,
    PRETTIER_DEFAULT_TIMEOUT,
    PRETTIER_FILE_PATTERNS,
    PrettierPlugin,
)

__all__ = [
    "PRETTIER_CONFIG_FILENAMES",
    "PRETTIER_DEFAULT_PRIORITY",
    "PRETTIER_DEFAULT_TIMEOUT",
    "PRETTIER_FILE_PATTERNS",
    "PrettierPlugin",
]
