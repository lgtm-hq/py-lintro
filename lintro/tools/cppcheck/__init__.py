"""Cppcheck tool package.

Everything the ``cppcheck`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.cppcheck.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.cppcheck.definition import (
    CPPCHECK_DEFAULT_ENABLE,
    CPPCHECK_DEFAULT_PRIORITY,
    CPPCHECK_DEFAULT_TIMEOUT,
    CPPCHECK_ERROR_EXITCODE,
    CPPCHECK_FILE_PATTERNS,
    CppcheckPlugin,
)

__all__ = [
    "CPPCHECK_DEFAULT_ENABLE",
    "CPPCHECK_DEFAULT_PRIORITY",
    "CPPCHECK_DEFAULT_TIMEOUT",
    "CPPCHECK_ERROR_EXITCODE",
    "CPPCHECK_FILE_PATTERNS",
    "CppcheckPlugin",
]
