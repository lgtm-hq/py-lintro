"""Buf tool package.

Everything the ``buf`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.buf.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.buf.definition import (
    BUF_DEFAULT_PRIORITY,
    BUF_DEFAULT_TIMEOUT,
    BUF_FILE_PATTERNS,
    BufPlugin,
)

__all__ = [
    "BUF_DEFAULT_PRIORITY",
    "BUF_DEFAULT_TIMEOUT",
    "BUF_FILE_PATTERNS",
    "BufPlugin",
]
