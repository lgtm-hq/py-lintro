"""Mypy tool package.

Everything the ``mypy`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.mypy.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.mypy.definition import (
    MYPY_DEFAULT_EXCLUDE_PATTERNS,
    MYPY_DEFAULT_PRIORITY,
    MYPY_DEFAULT_TIMEOUT,
    MYPY_FILE_PATTERNS,
    MYPY_NO_FILES_MARKER,
    MYPY_OPTION_TYPES,
    MypyPlugin,
)

__all__ = [
    "MYPY_DEFAULT_EXCLUDE_PATTERNS",
    "MYPY_DEFAULT_PRIORITY",
    "MYPY_DEFAULT_TIMEOUT",
    "MYPY_FILE_PATTERNS",
    "MYPY_NO_FILES_MARKER",
    "MYPY_OPTION_TYPES",
    "MypyPlugin",
]
