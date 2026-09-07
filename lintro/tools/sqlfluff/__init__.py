"""Sqlfluff tool package.

Everything the ``sqlfluff`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.sqlfluff.definition`. ``lintro.tools.definitions.sqlfluff``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.sqlfluff.definition import (
    SQLFLUFF_DEFAULT_FORMAT,
    SQLFLUFF_DEFAULT_PRIORITY,
    SQLFLUFF_DEFAULT_TIMEOUT,
    SQLFLUFF_FILE_PATTERNS,
    SqlfluffPlugin,
)

__all__ = [
    "SQLFLUFF_DEFAULT_FORMAT",
    "SQLFLUFF_DEFAULT_PRIORITY",
    "SQLFLUFF_DEFAULT_TIMEOUT",
    "SQLFLUFF_FILE_PATTERNS",
    "SqlfluffPlugin",
]
