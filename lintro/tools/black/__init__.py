"""Black tool package.

Everything the ``black`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.black.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.black.definition import (
    BLACK_DEFAULT_PRIORITY,
    BLACK_DEFAULT_TIMEOUT,
    BLACK_FILE_PATTERNS,
    BlackPlugin,
)

__all__ = [
    "BLACK_DEFAULT_PRIORITY",
    "BLACK_DEFAULT_TIMEOUT",
    "BLACK_FILE_PATTERNS",
    "BlackPlugin",
]
