"""Oxfmt tool package.

Everything the ``oxfmt`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.oxfmt.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.oxfmt.definition import (
    OXFMT_DEFAULT_PRIORITY,
    OXFMT_DEFAULT_TIMEOUT,
    OXFMT_FILE_PATTERNS,
    OxfmtPlugin,
)

__all__ = [
    "OXFMT_DEFAULT_PRIORITY",
    "OXFMT_DEFAULT_TIMEOUT",
    "OXFMT_FILE_PATTERNS",
    "OxfmtPlugin",
]
