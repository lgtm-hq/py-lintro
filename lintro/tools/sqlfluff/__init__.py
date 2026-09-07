"""Sqlfluff tool package.

Everything the ``sqlfluff`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.sqlfluff.definition`. Plugin discovery enters the package
through that module (#2311).
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
