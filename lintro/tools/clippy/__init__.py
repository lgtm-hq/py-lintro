"""Clippy tool package.

Everything the ``clippy`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.clippy.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.clippy.definition import (
    CLIPPY_DEFAULT_PRIORITY,
    CLIPPY_DEFAULT_TIMEOUT,
    CLIPPY_FILE_PATTERNS,
    ClippyPlugin,
)

__all__ = [
    "CLIPPY_DEFAULT_PRIORITY",
    "CLIPPY_DEFAULT_TIMEOUT",
    "CLIPPY_FILE_PATTERNS",
    "ClippyPlugin",
]
