"""Pydoclint tool package.

Everything the ``pydoclint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.pydoclint.definition`. ``lintro.tools.definitions.pydoclint``
re-exports the plugin so plugin discovery keeps finding it (#2311).
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
