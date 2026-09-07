"""Shfmt tool package.

Everything the ``shfmt`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.shfmt.definition`. ``lintro.tools.definitions.shfmt``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.shfmt.definition import (
    SHFMT_DEFAULT_PRIORITY,
    SHFMT_DEFAULT_TIMEOUT,
    SHFMT_FILE_PATTERNS,
    ShfmtPlugin,
)

__all__ = [
    "SHFMT_DEFAULT_PRIORITY",
    "SHFMT_DEFAULT_TIMEOUT",
    "SHFMT_FILE_PATTERNS",
    "ShfmtPlugin",
]
