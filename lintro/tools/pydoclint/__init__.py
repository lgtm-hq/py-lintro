"""Pydoclint tool package.

Everything the ``pydoclint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.pydoclint.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.pydoclint.definition import (
    PYDOCLINT_DEFAULT_PRIORITY,
    PYDOCLINT_DEFAULT_TIMEOUT,
    PYDOCLINT_FILE_PATTERNS,
    PydoclintPlugin,
)

__all__ = [
    "PYDOCLINT_DEFAULT_PRIORITY",
    "PYDOCLINT_DEFAULT_TIMEOUT",
    "PYDOCLINT_FILE_PATTERNS",
    "PydoclintPlugin",
]
