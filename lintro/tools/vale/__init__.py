"""Vale tool package.

Everything the ``vale`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.vale.definition`. ``lintro.tools.definitions.vale``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.vale.definition import (
    VALE_CONFIG_FILENAMES,
    VALE_DEFAULT_PRIORITY,
    VALE_DEFAULT_TIMEOUT,
    VALE_FILE_PATTERNS,
    ValePlugin,
)

__all__ = [
    "VALE_CONFIG_FILENAMES",
    "VALE_DEFAULT_PRIORITY",
    "VALE_DEFAULT_TIMEOUT",
    "VALE_FILE_PATTERNS",
    "ValePlugin",
]
