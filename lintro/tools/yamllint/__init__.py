"""Yamllint tool package.

Everything the ``yamllint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.yamllint.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.yamllint.definition import (
    YAMLLINT_DEFAULT_PRIORITY,
    YAMLLINT_DEFAULT_TIMEOUT,
    YAMLLINT_FILE_PATTERNS,
    YAMLLINT_FORMATS,
    YamllintPlugin,
)

__all__ = [
    "YAMLLINT_DEFAULT_PRIORITY",
    "YAMLLINT_DEFAULT_TIMEOUT",
    "YAMLLINT_FILE_PATTERNS",
    "YAMLLINT_FORMATS",
    "YamllintPlugin",
]
